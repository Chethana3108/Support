"""
Dynamic website ingestion with incremental sync.

Replaces the old hardcoded URL list approach with:
1. Automatic URL discovery via recursive BFS crawl
2. Content-hash-based change detection (only re-embeds what changed)
3. Tracks crawl state in the database for efficient incremental updates

Usage:
    python scripts/ingest.py              # Full incremental sync
    python scripts/ingest.py --force      # Force full re-ingest (drops all data)
"""

import asyncio
import hashlib
import logging
import sys
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Add workspace directory to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.models import Base, WebsiteChunk, CrawlState
from app.services.embedder import EmbedderService
from scripts.crawler import discover_urls, crawl_and_extract, content_hash

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest")


def chunk_documents(documents: List[dict]) -> List[dict]:
    """Split documents into smaller chunks for embedding, with overlap."""
    all_chunks = []
    for doc in documents:
        text = doc["content"]
        paragraphs = text.split("\n")
        current_chunk = ""

        for para in paragraphs:
            if len(current_chunk) + len(para) < settings.CHUNK_SIZE:
                current_chunk += para + "\n"
            else:
                if current_chunk.strip():
                    all_chunks.append({
                        "text": current_chunk.strip(),
                        "url": doc["url"],
                        "title": doc["title"],
                    })
                overlap_text = current_chunk[-settings.CHUNK_OVERLAP:] if len(current_chunk) > settings.CHUNK_OVERLAP else ""
                current_chunk = overlap_text + para + "\n"

        if current_chunk.strip():
            all_chunks.append({
                "text": current_chunk.strip(),
                "url": doc["url"],
                "title": doc["title"],
            })

    logger.info(f"Created {len(all_chunks)} chunks from {len(documents)} documents")
    return all_chunks


async def embed_and_store_chunks(db: AsyncSession, documents: List[dict]):
    """Chunk documents, generate embeddings, and store in database."""
    chunks = chunk_documents(documents)
    if not chunks:
        logger.info("No chunks to embed.")
        return 0

    # Generate embeddings
    logger.info(f"Generating embeddings for {len(chunks)} chunks...")
    texts = [c["text"] for c in chunks]
    embeddings = EmbedderService.encode(texts)

    for chunk, emb in zip(chunks, embeddings):
        chunk["embedding"] = emb

    # Insert into database
    for chunk in chunks:
        db_chunk = WebsiteChunk(
            url=chunk["url"],
            title=chunk["title"],
            content=chunk["text"],
            embedding=chunk["embedding"]
        )
        db.add(db_chunk)

    return len(chunks)


async def get_existing_crawl_state(db: AsyncSession) -> Dict[str, str]:
    """Fetch all active crawl_state entries. Returns {url: content_hash}."""
    stmt = select(CrawlState).where(CrawlState.status == "active")
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {row.url: row.content_hash for row in rows}


async def incremental_sync(db: AsyncSession, force: bool = False):
    """
    Perform incremental sync of website content:
    
    1. Discover all URLs via BFS crawl
    2. Fetch and extract content from each URL
    3. Compare content hashes against crawl_state:
       - New URLs → embed & insert
       - Changed URLs → delete old chunks, re-embed & insert
       - Removed URLs → delete chunks, mark as removed
       - Unchanged URLs → skip
    4. Update crawl_state table
    """
    now = datetime.now(timezone.utc)
    
    # Step 1: Discover all URLs
    logger.info(f"Discovering URLs from {settings.CRAWL_BASE_URL}...")
    discovered_urls = discover_urls(
        base_url=settings.CRAWL_BASE_URL,
        max_pages=settings.CRAWL_MAX_PAGES,
        max_depth=settings.CRAWL_MAX_DEPTH,
        max_workers=settings.CRAWL_CONCURRENT_WORKERS,
    )
    
    if not discovered_urls:
        logger.error("No URLs discovered. Aborting sync.")
        return
    
    logger.info(f"Discovered {len(discovered_urls)} URLs")
    
    # Step 2: Fetch and extract content
    documents = crawl_and_extract(
        urls=discovered_urls,
        max_workers=settings.CRAWL_CONCURRENT_WORKERS,
    )
    
    if not documents:
        logger.error("No content extracted from any URL. Aborting sync.")
        return
    
    # Build lookup: url -> document
    doc_map: Dict[str, dict] = {doc["url"]: doc for doc in documents}
    crawled_urls: Set[str] = set(doc_map.keys())
    
    # Step 3: Get existing crawl state
    if force:
        logger.info("Force mode: dropping all existing data...")
        await db.execute(delete(WebsiteChunk))
        await db.execute(delete(CrawlState))
        existing_state: Dict[str, str] = {}
    else:
        existing_state = await get_existing_crawl_state(db)
    
    existing_urls: Set[str] = set(existing_state.keys())
    
    # Classify URLs
    new_urls = crawled_urls - existing_urls
    removed_urls = existing_urls - crawled_urls
    potentially_changed_urls = crawled_urls & existing_urls
    
    changed_urls: Set[str] = set()
    unchanged_urls: Set[str] = set()
    
    for url in potentially_changed_urls:
        doc = doc_map[url]
        if doc["content_hash"] != existing_state[url]:
            changed_urls.add(url)
        else:
            unchanged_urls.add(url)
    
    logger.info(
        f"Sync analysis: {len(new_urls)} new, {len(changed_urls)} changed, "
        f"{len(removed_urls)} removed, {len(unchanged_urls)} unchanged"
    )
    
    total_chunks_added = 0
    
    # Step 4a: Process NEW URLs
    if new_urls:
        new_docs = [doc_map[url] for url in new_urls]
        logger.info(f"Embedding {len(new_docs)} new pages...")
        chunks_added = await embed_and_store_chunks(db, new_docs)
        total_chunks_added += chunks_added
        
        # Add crawl_state entries
        for url in new_urls:
            doc = doc_map[url]
            db.add(CrawlState(
                url=url,
                content_hash=doc["content_hash"],
                last_crawled_at=now,
                status="active",
            ))
    
    # Step 4b: Process CHANGED URLs
    if changed_urls:
        logger.info(f"Re-embedding {len(changed_urls)} changed pages...")
        
        # Delete old chunks for changed URLs
        for url in changed_urls:
            await db.execute(
                delete(WebsiteChunk).where(WebsiteChunk.url == url)
            )
        
        # Embed and insert new content
        changed_docs = [doc_map[url] for url in changed_urls]
        chunks_added = await embed_and_store_chunks(db, changed_docs)
        total_chunks_added += chunks_added
        
        # Update crawl_state
        for url in changed_urls:
            doc = doc_map[url]
            await db.execute(
                update(CrawlState)
                .where(CrawlState.url == url)
                .values(
                    content_hash=doc["content_hash"],
                    last_crawled_at=now,
                    status="active",
                )
            )
    
    # Step 4c: Process REMOVED URLs
    if removed_urls:
        logger.info(f"Removing {len(removed_urls)} deleted pages...")
        for url in removed_urls:
            await db.execute(
                delete(WebsiteChunk).where(WebsiteChunk.url == url)
            )
            await db.execute(
                update(CrawlState)
                .where(CrawlState.url == url)
                .values(status="removed", last_crawled_at=now)
            )
    
    # Step 4d: Update last_crawled_at for unchanged URLs
    if unchanged_urls:
        for url in unchanged_urls:
            await db.execute(
                update(CrawlState)
                .where(CrawlState.url == url)
                .values(last_crawled_at=now)
            )
    
    await db.commit()
    
    logger.info(
        f"Sync complete! "
        f"Added {total_chunks_added} chunks | "
        f"New pages: {len(new_urls)} | Changed: {len(changed_urls)} | "
        f"Removed: {len(removed_urls)} | Unchanged: {len(unchanged_urls)}"
    )


async def main():
    """Entry point for manual ingestion runs."""
    force = "--force" in sys.argv
    
    logger.info("Initializing DB Engine...")
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with AsyncSessionLocal() as db:
        try:
            await incremental_sync(db, force=force)
        except Exception as e:
            logger.error(f"Ingestion failed: {e}", exc_info=True)
            await db.rollback()
        finally:
            await db.close()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

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
import functools
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
from scripts.crawler import (
    discover_urls, crawl_and_extract, content_hash,
    fetch_sitemap_urls, fetch_sitemaps_from_robots, normalize_url
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest")


def chunk_documents(documents: List[dict]) -> List[dict]:
    """Split documents into semantic, section-aware chunks with title/URL context."""
    import re
    all_chunks = []
    for doc in documents:
        title = doc.get("title", "").strip()
        url = doc.get("url", "").strip()
        header = f"[Source: {title} | {url}]\n"

        raw_text = doc.get("content", "").strip()
        if not raw_text:
            continue

        paragraphs = [p.strip() for p in raw_text.split("\n") if p.strip()]
        if not paragraphs:
            continue

        current_paras: List[str] = []
        current_len = len(header)

        for para in paragraphs:
            para_len = len(para) + 1  # accounting for newline

            # If adding this paragraph exceeds CHUNK_SIZE and we already have content:
            if current_paras and (current_len + para_len > settings.CHUNK_SIZE):
                chunk_body = "\n".join(current_paras)
                all_chunks.append({
                    "text": f"{header}{chunk_body}",
                    "url": url,
                    "title": title,
                })
                # Paragraph-based overlap (preserves complete thoughts, never cuts words)
                overlap_paras: List[str] = []
                overlap_len = 0
                for prev in reversed(current_paras):
                    if overlap_len + len(prev) <= settings.CHUNK_OVERLAP:
                        overlap_paras.insert(0, prev)
                        overlap_len += len(prev)
                    else:
                        break
                current_paras = overlap_paras + [para]
                current_len = len(header) + sum(len(p) + 1 for p in current_paras)
            else:
                current_paras.append(para)
                current_len += para_len

        if current_paras:
            chunk_body = "\n".join(current_paras)
            all_chunks.append({
                "text": f"{header}{chunk_body}",
                "url": url,
                "title": title,
            })

    logger.info(f"Created {len(all_chunks)} semantic chunks from {len(documents)} documents")
    return all_chunks


async def embed_and_store_chunks(db: AsyncSession, documents: List[dict]):
    """Chunk documents, generate embeddings, and store in database."""
    chunks = chunk_documents(documents)
    if not chunks:
        logger.info("No chunks to embed.")
        return 0

    # Generate embeddings in a thread pool to avoid blocking the event loop
    logger.info(f"Generating embeddings for {len(chunks)} chunks...")
    texts = [c["text"] for c in chunks]
    loop = asyncio.get_event_loop()
    embeddings = await loop.run_in_executor(None, EmbedderService.encode, texts)

    for chunk, emb in zip(chunks, embeddings):
        chunk["embedding"] = emb

    # Insert into database
    for chunk in chunks:
        # Tag case study chunks with metadata for dedicated retrieval
        chunk_url = chunk["url"]
        is_case_study = (
            "/case-studies/" in chunk_url
            and chunk_url.rstrip("/") != chunk_url.split("/case-studies/")[0] + "/case-studies"
        )
        chunk_meta = {"type": "case_study"} if is_case_study else {}

        db_chunk = WebsiteChunk(
            url=chunk["url"],
            title=chunk["title"],
            content=chunk["text"],
            embedding=chunk["embedding"],
            meta=chunk_meta,
        )
        db.add(db_chunk)

    return len(chunks)


async def get_existing_crawl_state(db: AsyncSession) -> Dict[str, str]:
    """Fetch all active crawl_state entries. Returns {url: content_hash}."""
    stmt = select(CrawlState).where(CrawlState.status == "active")
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {row.url: row.content_hash for row in rows}


async def erase_database(db: AsyncSession, erase_all: bool = False):
    """Erase data from PostgreSQL."""
    if erase_all:
        logger.warning("Erasing ALL data across all tables...")
        from app.models import (
            User, Conversation, Message, LeadState,
            MemoryEmbedding, UserEpisodicMemory
        )
        await db.execute(delete(MemoryEmbedding))
        await db.execute(delete(UserEpisodicMemory))
        await db.execute(delete(LeadState))
        await db.execute(delete(Message))
        await db.execute(delete(Conversation))
        await db.execute(delete(User))
    
    logger.warning("Erasing website_chunks and crawl_state...")
    await db.execute(delete(WebsiteChunk))
    await db.execute(delete(CrawlState))
    await db.commit()
    logger.info("Database erasure complete.")


async def incremental_sync(
    db: AsyncSession,
    force: bool = False,
    sitemap_filter: Optional[str] = None,
    specific_urls: Optional[List[str]] = None,
):
    """
    Perform incremental sync of website content:
    
    1. Discover all URLs via robots.txt sitemaps or specific URLs
    2. Fetch and extract content from each URL
    3. Compare content hashes against crawl_state:
       - New URLs → embed & insert
       - Changed URLs → delete old chunks, re-embed & insert
       - Removed URLs → delete chunks, mark as removed
       - Unchanged URLs → skip
    4. Update crawl_state table
    """
    now = datetime.now(timezone.utc)
    loop = asyncio.get_event_loop()

    if specific_urls:
        discovered_urls = [normalize_url(u) for u in specific_urls]
        logger.info(f"Targeting {len(discovered_urls)} specific URLs")
    else:
        # Step 1: Discover sitemaps declared in robots.txt
        logger.info(f"Discovering sitemaps from {settings.CRAWL_BASE_URL}robots.txt...")
        all_sitemaps = await loop.run_in_executor(
            None, fetch_sitemaps_from_robots, settings.CRAWL_BASE_URL
        )

        if sitemap_filter:
            target_sitemaps = [s for s in all_sitemaps if sitemap_filter.lower() in s.lower()]
            logger.info(f"Targeting sitemaps matching '{sitemap_filter}': {target_sitemaps}")
        else:
            # If sitemap.xml exists in the list alongside sub-sitemaps, use sub-sitemaps to avoid duplicate recursion
            sub_sitemaps = [s for s in all_sitemaps if not s.endswith('/sitemap.xml')]
            target_sitemaps = sub_sitemaps if sub_sitemaps else all_sitemaps
            logger.info(f"Using sitemaps: {target_sitemaps}")

        sitemap_urls: List[str] = []
        for sm in target_sitemaps:
            fetched = await loop.run_in_executor(None, fetch_sitemap_urls, sm)
            sitemap_urls.extend(fetched)
            logger.info(f"Sitemap {sm}: {len(fetched)} URLs")

        # Deduplicate sitemap URLs
        sitemap_urls = list(dict.fromkeys(sitemap_urls))
        logger.info(f"Total unique sitemap seed URLs: {len(sitemap_urls)}")

        # Target all URLs directly from the declared sitemaps for fast, comprehensive coverage
        discovered_urls = sitemap_urls
    
    if not discovered_urls:
        logger.error("No URLs discovered. Aborting sync.")
        return
    
    logger.info(f"Discovered {len(discovered_urls)} URLs across sitemaps")
    
    # Fetch and extract content concurrently in thread pool
    crawl_workers = max(settings.CRAWL_CONCURRENT_WORKERS, 15)
    documents = await loop.run_in_executor(
        None,
        functools.partial(
            crawl_and_extract,
            urls=discovered_urls,
            max_workers=crawl_workers,
        )
    )
    
    if not documents:
        logger.error("No content extracted from any URL. Aborting sync.")
        return
     
    # Build lookup: url -> document
    doc_map: Dict[str, dict] = {doc["url"]: doc for doc in documents}
    crawled_urls: Set[str] = set(doc_map.keys())
    
    # Step 3: Get existing crawl state
    if force:
        if specific_urls:
            logger.info(f"Force mode for {len(specific_urls)} specific URLs: dropping chunks for those URLs...")
            for u in specific_urls:
                await db.execute(delete(WebsiteChunk).where(WebsiteChunk.url == u))
                await db.execute(delete(CrawlState).where(CrawlState.url == u))
            existing_state: Dict[str, str] = await get_existing_crawl_state(db)
            for u in specific_urls:
                existing_state.pop(u, None)
        else:
            logger.info("Force mode: dropping all existing data...")
            await db.execute(delete(WebsiteChunk))
            await db.execute(delete(CrawlState))
            existing_state: Dict[str, str] = {}
    else:
        existing_state = await get_existing_crawl_state(db)
    
    existing_urls: Set[str] = set(existing_state.keys())
    
    # Classify URLs
    new_urls = crawled_urls - existing_urls
    removed_urls = (existing_urls - crawled_urls) if not specific_urls else set()
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
    import argparse
    parser = argparse.ArgumentParser(description="Biztechnosys Website Ingestion & Incremental Sync")
    parser.add_argument("--force", action="store_true", help="Clear past website chunks and crawl state before syncing")
    parser.add_argument("--erase-all", action="store_true", help="Clear all database tables before syncing")
    parser.add_argument("--erase-only", action="store_true", help="Erase data without syncing new content")
    parser.add_argument("--sitemap", type=str, default=None, help="Target specific sitemap (e.g. pages-sitemap.xml)")
    parser.add_argument("--urls", type=str, default=None, help="Comma-separated specific URLs to ingest")

    args = parser.parse_args()

    logger.info("Initializing DB Engine...")
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with AsyncSessionLocal() as db:
        try:
            if args.erase_only:
                await erase_database(db, erase_all=args.erase_all)
            else:
                if args.erase_all:
                    await erase_database(db, erase_all=True)
                
                specific_urls = [u.strip() for u in args.urls.split(",") if u.strip()] if args.urls else None
                await incremental_sync(
                    db,
                    force=args.force or args.erase_all,
                    sitemap_filter=args.sitemap,
                    specific_urls=specific_urls,
                )
        except Exception as e:
            logger.error(f"Ingestion failed: {e}", exc_info=True)
            await db.rollback()
        finally:
            await db.close()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

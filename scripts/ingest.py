import asyncio
import logging
import re
import sys
import os
from typing import List, Dict, Any
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import httpx
from bs4 import BeautifulSoup, Comment
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Add workspace directory to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.models import Base, WebsiteChunk
from app.services.embedder import EmbedderService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest")

URLS_TO_SCRAPE = [
    "https://beta.biztechnosys.com/",
    "https://beta.biztechnosys.com/ai-sitecore/agentic-dxp-implementation",
    "https://beta.biztechnosys.com/ai-sitecore/migration-to-sitecore-in-8-weeks",
    "https://beta.biztechnosys.com/ai-sitecore/sitecore-cloud-managed-services",
    "https://beta.biztechnosys.com/ai-sitecore/sitecore-upgrade-in-6-weeks",
    "https://beta.biztechnosys.com/ai-sitecore/real-time-personalization",
    "https://beta.biztechnosys.com/ai-sitecore/audience-segmentation-ai",
    "https://beta.biztechnosys.com/ai-sitecore/ai-smart-search",
    "https://beta.biztechnosys.com/ai-sitecore/semantic-search",
    "https://beta.biztechnosys.com/ai-sitecore/documind-ai",
    "https://beta.biztechnosys.com/ai-sitecore/knowledgeconnect-ai",
    "https://beta.biztechnosys.com/ai-sitecore/facetrack-ai",
    "https://beta.biztechnosys.com/ai-sitecore/smartrecommend-ai",
    "https://beta.biztechnosys.com/ai-sitecore/agentflow-ai",
    "https://beta.biztechnosys.com/ai-sitecore/geosmart-ai",
    "https://beta.biztechnosys.com/solutions/legacy-to-headless-transformation",
    "https://beta.biztechnosys.com/solutions/migration-to-xm-cloud-in-8-weeks",
    "https://beta.biztechnosys.com/solutions/sitecore-cloud-managed-services",
    "https://beta.biztechnosys.com/solutions/composable-architecture-design",
    "https://beta.biztechnosys.com/solutions/headless-cms-strategy",
    "https://beta.biztechnosys.com/solutions/cdp-implementation--consulting",
    "https://beta.biztechnosys.com/solutions/customer-data-integration",
    "https://beta.biztechnosys.com/solutions/dam-implementation",
    "https://beta.biztechnosys.com/solutions/asset-organization--metadata",
    "https://beta.biztechnosys.com/solutions/247-sitecore-monitoring",
    "https://beta.biztechnosys.com/solutions/performance-optimization",
    "https://beta.biztechnosys.com/solutions/salesforce--sitecore",
    "https://beta.biztechnosys.com/solutions/sap-integration",
]

def clean_html(html: str, url: str) -> dict:

    soup = BeautifulSoup(html, "html.parser")

   
    for tag in soup(["script", "style", "noscript", "svg", "path", "meta", "link"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else url.split("/")[-1]

    article = soup.find("article")
    main_el = article if article else soup.find("main") or soup.find("body")
    if main_el is None:
        main_el = soup

    for tag in main_el.find_all(["header", "footer", "nav"]):
        tag.decompose()

    text = main_el.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.split("\n") if len(line.strip()) > 10]
    

    deduped = []
    for line in lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)

    return {"title": title, "content": "\n".join(deduped), "url": url}

def fetch_url(url: str) -> str:
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read().decode('utf-8', errors='ignore')

async def scrape_all_urls() -> List[dict]:

    documents = []
    logger.info(f"Starting crawl of {len(URLS_TO_SCRAPE)} URLs...")
    loop = asyncio.get_running_loop()
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = []
        for url in URLS_TO_SCRAPE:
            tasks.append(loop.run_in_executor(executor, fetch_url, url))
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    for url, resp in zip(URLS_TO_SCRAPE, responses):
        if not isinstance(resp, str):
            logger.warning(f"Failed to scrape {url}: {resp}")
            continue
        
        doc = clean_html(resp, url)
        if len(doc["content"]) > 50:
            documents.append(doc)
            logger.info(f"Scraped {url} ({len(doc['content'])} chars)")

    return documents

def chunk_documents(documents: List[dict]) -> List[dict]:
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

async def main():
    logger.info("Initializing DB Engine...")
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Scrape documents
    documents = await scrape_all_urls()
    if not documents:
        logger.error("No documents were scraped. Exiting.")
        return

    # Chunk documents
    chunks = chunk_documents(documents)
    if not chunks:
        logger.error("No chunks were created. Exiting.")
        return

    # Generate Embeddings
    logger.info("Generating embeddings for website chunks...")
    texts = [c["text"] for c in chunks]
    embeddings = EmbedderService.encode(texts)
    
    for chunk, emb in zip(chunks, embeddings):
        chunk["embedding"] = emb

    # Save to PostgreSQL
    logger.info("Writing to database...")
    async with AsyncSessionLocal() as db:
        try:
            # Drop existing chunks to refresh index
            await db.execute(delete(WebsiteChunk))
            
            # Batch inserts
            for chunk in chunks:
                db_chunk = WebsiteChunk(
                    url=chunk["url"],
                    title=chunk["title"],
                    content=chunk["text"],
                    embedding=chunk["embedding"]
                )
                db.add(db_chunk)
            
            await db.commit()
            logger.info("Successfully ingested chunks into PostgreSQL!")
        except Exception as e:
            logger.error(f"Error saving to database: {e}")
            await db.rollback()
        finally:
            await db.close()

if __name__ == "__main__":
    asyncio.run(main())

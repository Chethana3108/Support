"""Check actual content of case study chunks to diagnose quality issues."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from app.config import settings


async def check():
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        # Get a sample of case study chunks - first 3 chunks from each URL
        result = await conn.execute(text("""
            SELECT DISTINCT url, title FROM website_chunks 
            WHERE metadata->>'type' = 'case_study' 
            ORDER BY url LIMIT 12
        """))
        urls = result.fetchall()
        
        for url, title in urls:
            print(f"\n{'='*80}")
            print(f"URL: {url}")
            print(f"Title: {title}")
            
            # Get first chunk for this URL
            chunk_result = await conn.execute(text("""
                SELECT content FROM website_chunks 
                WHERE url = :url AND metadata->>'type' = 'case_study'
                LIMIT 1
            """), {"url": url})
            chunk = chunk_result.fetchone()
            if chunk:
                content = chunk[0][:500]
                print(f"Sample content ({len(chunk[0])} chars total):")
                print(f"  {content}")
            print()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(check())

"""Quick script to create the crawl_state table in the database."""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.config import settings

async def run():
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crawl_state (
                url TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                last_crawled_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'active'
            )
        """))
        print("crawl_state table created successfully!")
    await engine.dispose()

asyncio.run(run())

"""Run the migration to add metadata column to website_chunks."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.config import settings


async def run_migration():
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE website_chunks ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb"
        ))
        print("Migration successful: metadata column added to website_chunks")
        
        # Verify it exists
        result = await conn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'website_chunks' AND column_name = 'metadata'"
        ))
        row = result.fetchone()
        if row:
            print(f"  Verified: column '{row[0]}' of type '{row[1]}' exists")
        else:
            print("  WARNING: column not found after migration!")
    
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_migration())

"""
Script to wipe all existing data from all tables in the database.
Preserves the table schema, constraints, sequences, and extensions intact.

Usage:
    python scripts/wipe_database.py
"""
import asyncio
import os
import sys

# Add project root to python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.config import settings

TABLES = [
    "memory_embeddings",
    "user_episodic_memories",
    "messages",
    "lead_state",
    "conversations",
    "users",
    "website_chunks",
    "crawl_state",
]

async def wipe_all_data():
    print(f"Connecting to database configured in .env...")
    engine = create_async_engine(settings.DATABASE_URL)
    
    async with engine.begin() as conn:
        print("Executing TRUNCATE on all 8 tables with CASCADE & RESTART IDENTITY...")
        # TRUNCATE with CASCADE empties all rows and resets identity counters (e.g. SERIAL id)
        truncate_sql = "TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in TABLES) + " RESTART IDENTITY CASCADE;"
        await conn.execute(text(truncate_sql))
        print("Data truncation complete.")

    # Verification check
    print("\nVerifying row counts in all tables:")
    async with engine.connect() as conn:
        all_empty = True
        for t in TABLES:
            res = await conn.execute(text(f'SELECT COUNT(*) FROM "{t}";'))
            count = res.scalar()
            status = "CLEARED (0 rows)" if count == 0 else f"WARNING ({count} rows remaining)"
            print(f"  - {t}: {status}")
            if count != 0:
                all_empty = False

    await engine.dispose()
    
    if all_empty:
        print("\nSUCCESS: All tables have been completely cleared and reset!")
    else:
        print("\nWARNING: Some tables could not be fully cleared. Check foreign keys or permissions.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Wipe all data from database tables.")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if args.yes:
        asyncio.run(wipe_all_data())
    else:
        confirm = input("Are you sure you want to delete ALL data from the database? (yes/no): ").strip().lower()
        if confirm in ("yes", "y"):
            asyncio.run(wipe_all_data())
        else:
            print("Operation cancelled. No data was deleted.")


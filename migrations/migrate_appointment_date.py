"""One-time migration: Add appointment_date column to lead_state table."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.database import engine

async def migrate():
    async with engine.begin() as conn:
        # Check if column already exists
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'lead_state' AND column_name = 'appointment_date'"
        ))
        if result.fetchone():
            print("Column 'appointment_date' already exists in 'lead_state'. No migration needed.")
            return

        # Add the column
        await conn.execute(text(
            "ALTER TABLE lead_state ADD COLUMN appointment_date VARCHAR DEFAULT ''"
        ))
        print("SUCCESS: Added 'appointment_date' column to 'lead_state' table.")

if __name__ == "__main__":
    asyncio.run(migrate())

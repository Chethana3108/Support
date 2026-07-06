import asyncio
import os
from sqlalchemy import text
from app.database import engine

async def run_migration():
    migration_path = os.path.join("migrations", "002_user_identity.sql")
    if not os.path.exists(migration_path):
        print(f"Error: Migration file not found at {migration_path}")
        return

    print(f"Reading migration file: {migration_path}...")
    with open(migration_path, "r", encoding="utf-8") as f:
        sql_script = f.read()

    print("Connecting to database and running migration...")
    async with engine.begin() as conn:
        # Execute the raw SQL commands
        await conn.execute(text(sql_script))
        print("Migration executed successfully!")

if __name__ == "__main__":
    asyncio.run(run_migration())

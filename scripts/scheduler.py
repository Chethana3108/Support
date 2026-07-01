"""
Background scheduler for automatic website re-crawling and ingestion.

Can run in two modes:
1. Standalone: python scripts/scheduler.py
2. Integrated: imported and started as a background task in FastAPI lifespan

Runs incremental sync on a configurable interval (default: every 6 hours).
"""

import asyncio
import logging
import sys
import os
from typing import Optional

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Add workspace directory to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scheduler")


async def run_sync_cycle():
    """Execute a single sync cycle with its own DB session."""
    # Import here to avoid circular imports when used in FastAPI
    from scripts.ingest import incremental_sync
    
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    try:
        async with AsyncSessionLocal() as db:
            try:
                await incremental_sync(db, force=False)
            except Exception as e:
                logger.error(f"Sync cycle failed: {e}", exc_info=True)
                await db.rollback()
            finally:
                await db.close()
    finally:
        await engine.dispose()


async def scheduler_loop(
    interval_hours: Optional[float] = None,
    initial_delay_seconds: float = 60,
    run_once: bool = False,
):
    """
    Main scheduler loop. Runs incremental sync periodically.
    
    Args:
        interval_hours: Hours between sync cycles. Defaults to settings.CRAWL_INTERVAL_HOURS.
        initial_delay_seconds: Seconds to wait before the first sync (lets FastAPI warm up).
        run_once: If True, run a single sync and exit (useful for testing).
    """
    if interval_hours is None:
        interval_hours = settings.CRAWL_INTERVAL_HOURS
    
    interval_seconds = interval_hours * 3600
    
    logger.info(
        f"Crawler scheduler started | "
        f"Interval: {interval_hours}h | "
        f"Base URL: {settings.CRAWL_BASE_URL} | "
        f"Initial delay: {initial_delay_seconds}s"
    )
    
    # Wait for initial delay (let the server warm up)
    if initial_delay_seconds > 0:
        logger.info(f"Waiting {initial_delay_seconds}s before first sync...")
        await asyncio.sleep(initial_delay_seconds)
    
    cycle = 0
    while True:
        cycle += 1
        logger.info(f"=== Sync cycle #{cycle} starting ===")
        
        try:
            await run_sync_cycle()
            logger.info(f"=== Sync cycle #{cycle} completed ===")
        except Exception as e:
            logger.error(f"=== Sync cycle #{cycle} failed: {e} ===", exc_info=True)
        
        if run_once:
            logger.info("Run-once mode: exiting after single sync.")
            break
        
        logger.info(f"Next sync in {interval_hours} hours...")
        await asyncio.sleep(interval_seconds)


async def main():
    """Standalone entry point — runs the scheduler indefinitely."""
    await scheduler_loop(initial_delay_seconds=0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user.")

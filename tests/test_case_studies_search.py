"""Test search_knowledge and search_case_studies to see what is returned."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings
from app.services.knowledge import KnowledgeService


async def test_queries():
    engine = create_async_engine(settings.DATABASE_URL)
    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    
    queries = [
        "I want to upgrade Sitecore",
        "We need a healthcare solution",
        "hi, my name is John, we are looking for sitecore headless migration",
    ]
    
    async with AsyncSessionLocal() as db:
        for query in queries:
            print(f"\n==================================================")
            print(f"QUERY: '{query}'")
            
            # Test default threshold (0.45)
            cs_results_45 = await KnowledgeService.search_case_studies(
                db, query, threshold=0.45
            )
            print(f"\n--- Case Studies (Threshold 0.45) - Found: {len(cs_results_45)} ---")
            for cs in cs_results_45:
                print(f"  - Title: {cs['title']} (Score: {cs['score']:.3f})")
                print(f"    URL: {cs['url']}")
                
            # Test lower threshold (0.30)
            cs_results_30 = await KnowledgeService.search_case_studies(
                db, query, threshold=0.30
            )
            print(f"\n--- Case Studies (Threshold 0.30) - Found: {len(cs_results_30)} ---")
            for cs in cs_results_30:
                print(f"  - Title: {cs['title']} (Score: {cs['score']:.3f})")
                print(f"    URL: {cs['url']}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(test_queries())

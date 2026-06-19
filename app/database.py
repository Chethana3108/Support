import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

logger = logging.getLogger("biztechbot")

# Create Async Engine with settings optimized for PgBouncer compatibility
# We disable the prepared statement cache (prepared_statement_cache_size=0)
# to allow safe execution in PgBouncer transaction pooling mode.
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_recycle=1800,
    pool_pre_ping=True,
    echo=False,
    connect_args={
        "prepared_statement_cache_size": 0,
        "command_timeout": 30
    }
)

# Async Session Factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Declarative Base for models
class Base(DeclarativeBase):
    pass

# DB Session dependency generator
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

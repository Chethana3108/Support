import logging
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, engine
from app.routers import chat, sessions


logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "message": "%(message)s"}',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("biztechbot")

# Custom client IP resolver for proxy-safe rate limiting
def get_client_ip(request: Request) -> str:
    """Retrieve the real client IP address, checking headers for reverse proxies."""
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip
        
    # Check X-Forwarded-For header
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
        
    # Fallback to direct client host
    if request.client and request.client.host:
        return request.client.host
        
    return "127.0.0.1"

# Initialize SlowAPI rate limiter
limiter = Limiter(key_func=get_client_ip, default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"])

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for FastAPI. Startup and Shutdown."""
    logger.info("Initializing FastAPI chatbot platform...")    
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            logger.info("[OK] Database connection verified successfully.")
    except Exception as e:
        logger.critical(f"[ERROR] Database connection failed on startup: {e}")
    
    yield
    
    logger.info("Shutting down chatbot platform. Closing DB pool...")
    await engine.dispose()
    logger.info("[OK] Database pool closed.")


app = FastAPI(
    title="Biztechnosys AI Support Bot",
    description="Production-grade AI sales assistant using PostgreSQL RAG & pgvector memory.",
    version="3.1.0",
    lifespan=lifespan
)

# Configure CORS allowed origins from settings
origins = [origin.strip() for origin in settings.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set rate limiter in app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore


# Request tracing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    logger.info(f"path={request.url.path} method={request.method} latency={process_time:.4f}s status={response.status_code}")
    return response

# Custom HTTP exception handling for clean error logs
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    logger.error(f"HTTP exception: {exc.detail} status={exc.status_code}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )

# Include Routers
app.include_router(chat.router)
app.include_router(sessions.router)

# Health Endpoint
@app.get("/api/health", tags=["Operations"])
async def health(db: AsyncSession = Depends(get_db)):
    """Health check endpoint. Confirms database health and chunk counts."""
    db_alive = False
    chunks_count = 0
    
    try:
        # Ping Database
        await db.execute(text("SELECT 1"))
        db_alive = True
        
        # Query total ingested website knowledge chunks
        from app.models import WebsiteChunk
        stmt = select(func.count(WebsiteChunk.id))
        result = await db.execute(stmt)
        chunks_count = result.scalar() or 0
    except Exception as e:
        logger.error(f"Health check failed: {e}")

    status = "ok" if db_alive else "error"
    return {
        "status": status,
        "database": "connected" if db_alive else "disconnected",
        "chunks_indexed": chunks_count,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)




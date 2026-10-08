import logging
import httpx
from fastapi import HTTPException
from app.config import settings

logger = logging.getLogger("biztechbot")

_client: httpx.AsyncClient | None = None

def get_http_client() -> httpx.AsyncClient:
    """Reuses persistent HTTP connection pool to eliminate SSL/TCP handshake latency."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
        )
    return _client

async def call_deepseek(messages: list) -> str:
    """Call DeepSeek chat API with connection reuse."""
    client = get_http_client()
    try:
        response = await client.post(
            f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 500,
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except httpx.HTTPError as e:
        logger.error(f"HTTP error calling DeepSeek: {e}")
        raise HTTPException(status_code=502, detail="Failed to connect to DeepSeek API.")
    except Exception as e:
        logger.error(f"Unexpected error calling DeepSeek: {e}")
        raise HTTPException(status_code=500, detail="Internal AI error.")

def stream_deepseek(messages: list):
    """Returns an async context manager streaming from DeepSeek with connection reuse."""
    client = get_http_client()
    return client.stream(
        "POST",
        f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 500,
            "stream": True,
        }
    )

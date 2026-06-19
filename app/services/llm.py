import logging
import httpx
from fastapi import HTTPException
from app.config import settings

logger = logging.getLogger("biztechbot")

async def call_deepseek(messages: list) -> str:
    """Call DeepSeek chat API with timeout and authorization headers."""
    async with httpx.AsyncClient(timeout=60) as client:
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
                    "temperature": 0.7,
                    "max_tokens": 1024,
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

import asyncio
import logging
import functools
from typing import List, Optional
import numpy as np
from sentence_transformers import SentenceTransformer
from app.config import settings

logger = logging.getLogger("biztechbot")

class EmbedderService:
    _model: Optional[SentenceTransformer] = None

    @classmethod
    def get_model(cls) -> SentenceTransformer:
        """Lazy load the SentenceTransformer model to optimize startup memory usage."""
        if cls._model is None:
            logger.debug(f"Loading SentenceTransformer model: {settings.EMBEDDING_MODEL}")
            cls._model = SentenceTransformer(settings.EMBEDDING_MODEL)
            logger.debug("SentenceTransformer model loaded")
        return cls._model

    @classmethod
    def encode(cls, texts: List[str]) -> List[List[float]]:
        """Generate normalized embeddings for a list of texts."""
        if not texts:
            return []
        model = cls.get_model()
        # normalize_embeddings=True yields unit-length embeddings (cosine similarity = dot product)
        embeddings = model.encode(texts, batch_size=128, normalize_embeddings=True, show_progress_bar=False)
        return embeddings.tolist()

    @classmethod
    @functools.lru_cache(maxsize=4096)
    def encode_single(cls, text: str) -> List[float]:
        """Generate normalized embedding for a single text. Cached using LRU cache."""
        return cls.encode([text])[0]

    @classmethod
    async def encode_async(cls, texts: List[str]) -> List[List[float]]:
        """Async wrapper: runs encode() in a thread pool to avoid blocking the event loop."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, cls.encode, texts)

    @classmethod
    async def encode_single_async(cls, text: str) -> List[float]:
        """Async wrapper: runs encode_single() in a thread pool to avoid blocking the event loop."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, cls.encode_single, text)

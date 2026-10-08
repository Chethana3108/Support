import asyncio
import logging
from typing import List, Dict, Any, Callable
from sentence_transformers import CrossEncoder

logger = logging.getLogger("biztechbot")

class RerankerService:
    _model: CrossEncoder | None = None

    @classmethod
    def get_model(cls) -> CrossEncoder:
        """Lazy load the CrossEncoder model to optimize startup memory."""
        if cls._model is None:
            logger.debug("Loading CrossEncoder model: cross-encoder/ms-marco-MiniLM-L-6-v2")
            cls._model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            logger.debug("CrossEncoder model loaded")
        return cls._model

    @classmethod
    def rerank(
        cls, 
        query: str, 
        items: List[Dict[str, Any]], 
        text_extractor: Callable[[Dict[str, Any]], str],
        top_k: int
    ) -> List[Dict[str, Any]]:
        """
        Reranks a list of candidate items relative to the query.
        
        - query: The search query.
        - items: List of dicts representing candidates.
        - text_extractor: Function mapping item dict to its content text.
        - top_k: Number of highest-ranked items to return.
        """
        if not items:
            return []

        model = cls.get_model()
        pairs: List[Any] = [[query, text_extractor(item)] for item in items]
        
        # Predict similarity scores with optimized batch size for CPU vectorization
        scores = model.predict(pairs, batch_size=16, show_progress_bar=False)
        
        # Attach scores to the candidate dicts, boosting exact keyword matches
        for item, score in zip(items, scores):
            final_score = float(score)
            if item.get("is_exact_match"):
                final_score += 2.5
            item["rerank_score"] = final_score
            
        # Sort descending by rerank score
        reranked = sorted(items, key=lambda x: x["rerank_score"], reverse=True)
        
        # Filter out low-relevance results to prevent hallucination from bad context.
        # ms-marco-MiniLM outputs logit scores where negative values indicate low relevance.
        MIN_RERANK_SCORE = -3.5
        before_count = len(reranked)
        reranked = [item for item in reranked if item["rerank_score"] > MIN_RERANK_SCORE or item.get("is_exact_match")]
        if before_count != len(reranked):
            logger.debug(f"Filtered {before_count - len(reranked)} low-relevance candidates (score <= {MIN_RERANK_SCORE})")
        
        logger.debug(f"Reranked {len(items)} candidates down to top {min(top_k, len(reranked))}")
        return reranked[:top_k]

    @classmethod
    async def rerank_async(
        cls,
        query: str,
        items: List[Dict[str, Any]],
        text_extractor: Callable[[Dict[str, Any]], str],
        top_k: int
    ) -> List[Dict[str, Any]]:
        """Async wrapper: runs rerank() in a thread pool to avoid blocking the event loop."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, cls.rerank, query, items, text_extractor, top_k)

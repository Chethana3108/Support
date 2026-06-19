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
            logger.info("Loading CrossEncoder model: cross-encoder/ms-marco-MiniLM-L-6-v2...")
            cls._model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            logger.info("[OK] CrossEncoder model successfully loaded.")
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
        
        # Predict similarity scores
        # ms-marco-MiniLM outputs a raw logit score where higher is more relevant.
        scores = model.predict(pairs)
        
        # Attach scores to the candidate dicts
        for item, score in zip(items, scores):
            item["rerank_score"] = float(score)
            
        # Sort descending by rerank score
        reranked = sorted(items, key=lambda x: x["rerank_score"], reverse=True)
        
        logger.info(f"Reranked {len(items)} candidates down to top {min(top_k, len(reranked))}")
        return reranked[:top_k]

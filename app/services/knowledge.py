import logging
from typing import List, Dict, Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import WebsiteChunk
from app.services.embedder import EmbedderService
from app.services.reranker import RerankerService
from app.config import settings

logger = logging.getLogger("biztechbot")

class KnowledgeService:
    @staticmethod
    async def search_knowledge(
        db: AsyncSession, 
        query: str, 
        candidate_k: Optional[int] = None, 
        final_k: Optional[int] = None,
        threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Perform high-precision website knowledge search using pgvector and Reranker:
        
        1. Embed the query and retrieve candidates using pgvector (up to candidate_k, default 20).
        2. Apply similarity threshold filtering (default >= 0.45).
        3. Deduplicate candidates by content.
        4. Rerank unique candidates using a Cross-Encoder.
        5. Return the top final_k candidates (default 5).
        """
        if candidate_k is None:
            candidate_k = settings.TOP_K_KNOWLEDGE
        if final_k is None:
            final_k = settings.TOP_K_RERANKED
        if threshold is None:
            threshold = settings.SIMILARITY_THRESHOLD_KNOWLEDGE

        # Generate normalized query embedding (hits LRU cache)
        query_embedding = EmbedderService.encode_single(query)

        # In pgvector: <=> operator represents cosine distance
        # Cosine Similarity = 1.0 - Cosine Distance
        cosine_distance = WebsiteChunk.embedding.cosine_distance(query_embedding).label("distance")
        max_distance = 1.0 - threshold
        
        stmt = (
            select(WebsiteChunk, cosine_distance)
            .where(WebsiteChunk.embedding.cosine_distance(query_embedding) <= max_distance)
            .order_by("distance")
            .limit(candidate_k * 2) # Fetch extra to account for deduplication
        )

        result = await db.execute(stmt)
        rows = result.all()

        candidates = []
        seen_content = set()
        
        for chunk, dist in rows:
            similarity = 1.0 - dist
            text_content = chunk.content.strip()
            
            if text_content in seen_content:
                continue
            
            seen_content.add(text_content)
            candidates.append({
                "text": text_content,
                "url": chunk.url,
                "title": chunk.title,
                "score": float(similarity)
            })

            # Fetch up to candidate_k unique items for reranking
            if len(candidates) >= candidate_k:
                break

        if not candidates:
            return []

        # Rerank candidates using Cross-Encoder
        reranked_results = RerankerService.rerank(
            query=query,
            items=candidates,
            text_extractor=lambda item: item["text"],
            top_k=final_k
        )
        
        return reranked_results

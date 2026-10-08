import logging
import re
from typing import List, Dict, Any, Optional
from sqlalchemy import select, or_
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
        threshold: Optional[float] = None,
        query_embedding: Optional[List[float]] = None,
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

        # Generate normalized query embedding if not provided (hits LRU cache)
        if query_embedding is None:
            query_embedding = await EmbedderService.encode_single_async(query)

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
                "id": chunk.id,
                "text": text_content,
                "url": chunk.url,
                "title": chunk.title,
                "score": float(similarity)
            })

            # Fetch up to candidate_k unique items for reranking
            if len(candidates) >= candidate_k:
                break

        # Hybrid retrieval: Also fetch candidates matching exact query phrases or entity terms
        # (critical for proper nouns and leadership titles like 'Uday Sanghani', 'Sitecore Practice Head', etc.)
        clean_q = re.sub(r'[^\w\s-]', ' ', query).strip()
        GENERIC_STOPWORDS = {
            'biztechnosys', 'sitecore', 'company', 'what', 'who', 'when', 'where', 'which', 'how',
            'many', 'much', 'does', 'have', 'with', 'tell', 'about', 'from', 'into', 'is', 'are',
            'was', 'were', 'the', 'your', 'our', 'their', 'this', 'that', 'these', 'those', 'and',
            'for', 'not', 'can', 'will', 'would', 'could', 'should', 'please', 'give', 'any', 'all',
            'some', 'them', 'they', 'than', 'then', 'also', 'just', 'more'
        }
        words = [w.strip() for w in clean_q.split() if len(w.strip()) >= 3 and w.lower() not in GENERIC_STOPWORDS]

        # Hybrid retrieval: If pgvector found fewer than 5 candidates, or for specific proper nouns/entities,
        # supplement with keyword matching (title ILIKE is indexed/fast; content ILIKE only as last resort)
        has_proper_nouns = any(w[0].isupper() for w in clean_q.split() if len(w) > 2)
        if len(candidates) < 5 or has_proper_nouns:
            kw_conditions = []
            if len(words) >= 2:
                for i in range(len(words) - 1):
                    phrase = f"{words[i]} {words[i+1]}"
                    kw_conditions.append(WebsiteChunk.title.ilike(f"%{phrase}%"))
                    if len(candidates) < 4:
                        kw_conditions.append(WebsiteChunk.content.ilike(f"%{phrase}%"))
            elif len(words) == 1 and (words[0].isupper() or len(words[0]) >= 5):
                kw_conditions.append(WebsiteChunk.title.ilike(f"%{words[0]}%"))
                if len(candidates) < 4:
                    kw_conditions.append(WebsiteChunk.content.ilike(f"%{words[0]}%"))

            if kw_conditions:
                stmt_kw = select(WebsiteChunk).where(or_(*kw_conditions)).limit(6)
                res_kw = await db.execute(stmt_kw)
                for chunk in res_kw.scalars().all():
                    text_content = chunk.content.strip()
                    if text_content not in seen_content:
                        seen_content.add(text_content)
                        candidates.append({
                            "id": chunk.id,
                            "text": text_content,
                            "url": chunk.url,
                            "title": chunk.title,
                            "score": 0.5,
                            "is_exact_match": False
                        })

        # Exact match flagging: only flag if document genuinely contains the exact query phrase or proper entity
        for c in candidates:
            text_lower = c["text"].lower()
            if len(clean_q) >= 10 and clean_q.lower() in text_lower:
                c["is_exact_match"] = True
            elif len(words) >= 2:
                exact_phrases_count = sum(1 for i in range(len(words)-1) if f"{words[i]} {words[i+1]}".lower() in text_lower)
                if exact_phrases_count >= 1:
                    c["is_exact_match"] = True

        # Guarantee leadership team chunk retrieval for any leadership, team, or executive queries
        leadership_terms = [
            'practice head', 'head', 'cto', 'ceo', 'coo', 'founder', 'director', 'leadership', 'team',
            'uday', 'manish', 'ramachandran', 'kalpesh', 'sanghani', 'vaza'
        ]
        if any(term in clean_q.lower() for term in leadership_terms):
            stmt_lead = select(WebsiteChunk).where(WebsiteChunk.content.ilike('%Meet Our Team%')).limit(2)
            res_lead = await db.execute(stmt_lead)
            for chunk in res_lead.scalars().all():
                text_content = chunk.content.strip()
                if text_content not in seen_content:
                    seen_content.add(text_content)
                    candidates.append({
                        "id": chunk.id,
                        "text": text_content,
                        "url": chunk.url,
                        "title": chunk.title,
                        "score": 0.5,
                        "is_exact_match": True
                    })
                else:
                    for c in candidates:
                        if c["text"] == text_content:
                            c["is_exact_match"] = True

        # Guarantee office location & contact chunk retrieval for location, office, address, and headquarters queries
        location_terms = [
            'office', 'offices', 'location', 'locations', 'headquarter', 'headquarters', 'hq',
            'address', 'addresses', 'contact', 'bengaluru', 'bangalore', 'ahmedabad', 'navsari'
        ]
        if any(term in clean_q.lower() for term in location_terms):
            stmt_loc = select(WebsiteChunk).where(
                or_(
                    WebsiteChunk.title.ilike('%Office Locations%'),
                    WebsiteChunk.content.ilike('%Biztechnosys Office Locations%'),
                    WebsiteChunk.url.ilike('%/contact%')
                )
            ).limit(4)
            res_loc = await db.execute(stmt_loc)
            for chunk in res_loc.scalars().all():
                text_content = chunk.content.strip()
                if text_content not in seen_content:
                    seen_content.add(text_content)
                    candidates.append({
                        "id": chunk.id,
                        "text": text_content,
                        "url": chunk.url,
                        "title": chunk.title,
                        "score": 0.5,
                        "is_exact_match": True
                    })
                else:
                    for c in candidates:
                        if c["text"] == text_content:
                            c["is_exact_match"] = True

        if not candidates:
            return []

        # Rerank candidates using Cross-Encoder with Title context (optimized to 600 chars for sub-second CPU inference)
        reranked_results = await RerankerService.rerank_async(
            query=query,
            items=candidates,
            text_extractor=lambda item: f"{item['title']} | {item['text'][:600]}",
            top_k=final_k
        )
        
        # Sibling/Neighbor Chunk Expansion (batch lookup for explicit sequential phase markers)
        expanded_results = list(reranked_results)
        expanded_ids = {r.get("id") for r in reranked_results if r.get("id")}
        
        next_ids_to_fetch = []
        for item in reranked_results:
            chunk_id = item.get("id")
            item_text = item.get("text", "")
            if chunk_id and any(kw in item_text for kw in ["Phase 1", "Phase 2", "Step 1", "Step 2", "PHASE 1", "PHASE 2"]):
                next_id = chunk_id + 1
                if next_id not in expanded_ids:
                    next_ids_to_fetch.append(next_id)
        
        if next_ids_to_fetch:
            next_stmt = select(WebsiteChunk).where(WebsiteChunk.id.in_(next_ids_to_fetch))
            next_res = await db.execute(next_stmt)
            for next_chunk in next_res.scalars().all():
                if next_chunk.id not in expanded_ids:
                    expanded_ids.add(next_chunk.id)
                    expanded_results.append({
                        "id": next_chunk.id,
                        "text": next_chunk.content.strip(),
                        "url": next_chunk.url,
                        "title": next_chunk.title,
                        "score": 0.75
                    })

        return expanded_results

    @staticmethod
    async def search_case_studies(
        db: AsyncSession,
        query: str,
        candidate_k: int = 10,
        final_k: int = 2,
        threshold: Optional[float] = None,
        query_embedding: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search specifically for case study chunks tagged with metadata type='case_study'.
        
        Uses the same vector similarity + cross-encoder reranking pipeline as
        search_knowledge, but filters to only case study content. Returns the
        top `final_k` (default 2) most relevant case studies.
        
        Includes boilerplate filtering: content that appears across 3+ different
        case study URLs is excluded, ensuring only unique case study narratives
        (project descriptions, challenges, outcomes) are considered.
        """
        if threshold is None:
            threshold = settings.SIMILARITY_THRESHOLD_KNOWLEDGE

        if query_embedding is None:
            query_embedding = await EmbedderService.encode_single_async(query)

        cosine_distance = WebsiteChunk.embedding.cosine_distance(query_embedding).label("distance")
        max_distance = 1.0 - threshold

        # Fetch extra candidates to compensate for boilerplate filtering
        stmt = (
            select(WebsiteChunk, cosine_distance)
            .where(
                WebsiteChunk.embedding.cosine_distance(query_embedding) <= max_distance,
                WebsiteChunk.meta["type"].astext == "case_study",
            )
            .order_by("distance")
            .limit(candidate_k * 5)
        )

        result = await db.execute(stmt)
        rows = result.all()

        # ── Boilerplate Detection ──────────────────────────────────────────
        # Content appearing in 3+ different case study URLs is shared
        # boilerplate (FAQ sections, service descriptions, nav items) and
        # should be excluded so the reranker only sees actual case study
        # narratives unique to each page.
        BOILERPLATE_URL_THRESHOLD = 3
        content_to_urls: dict[str, set[str]] = {}
        for chunk, _dist in rows:
            text = chunk.content.strip()
            if text not in content_to_urls:
                content_to_urls[text] = set()
            content_to_urls[text].add(chunk.url)

        boilerplate_texts = {
            text for text, urls in content_to_urls.items()
            if len(urls) >= BOILERPLATE_URL_THRESHOLD
        }

        if boilerplate_texts:
            logger.debug(
                f"Case study search: filtered {len(boilerplate_texts)} boilerplate "
                f"content chunks (appearing in {BOILERPLATE_URL_THRESHOLD}+ URLs)"
            )

        # ── Build Candidates (excluding boilerplate) ───────────────────────
        candidates = []
        seen_urls = set()

        for chunk, dist in rows:
            text_content = chunk.content.strip()

            # Skip boilerplate content shared across multiple case study pages
            if text_content in boilerplate_texts:
                continue

            similarity = 1.0 - dist

            # Deduplicate by URL so we get distinct case studies
            if chunk.url in seen_urls:
                # Keep the chunk but don't add a new candidate — merge text
                for c in candidates:
                    if c["url"] == chunk.url:
                        # Append additional chunk text for richer context
                        if len(c["text"]) < 1500:
                            c["text"] += "\n" + text_content
                        break
                continue

            seen_urls.add(chunk.url)
            candidates.append({
                "text": text_content,
                "url": chunk.url,
                "title": chunk.title,
                "score": float(similarity),
            })

            if len(candidates) >= candidate_k:
                break

        if not candidates:
            return []

        # Rerank candidates using Cross-Encoder with Title context (optimized to 600 chars for fast CPU inference)
        reranked_results = await RerankerService.rerank_async(
            query=query,
            items=candidates,
            text_extractor=lambda item: f"{item['title']} | {item['text'][:600]}",
            top_k=final_k,
        )

        return reranked_results


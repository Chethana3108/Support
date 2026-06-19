import logging
import uuid
from typing import List, Dict, Any, Set
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, Conversation, Message, MemoryEmbedding, UserEpisodicMemory
from app.services.embedder import EmbedderService
from app.services.reranker import RerankerService
from app.services.llm import call_deepseek
from app.config import settings

logger = logging.getLogger("biztechbot")

class MemoryService:
    @staticmethod
    async def create_user_if_not_exists(db: AsyncSession, user_id: str) -> User:
        """Create a new user account if it doesn't already exist."""
        stmt = select(User).where(User.user_id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            user = User(user_id=user_id)
            db.add(user)
            await db.commit()
            logger.info(f"Created new user account: {user_id}")
        return user

    @classmethod
    async def create_conversation_if_not_exists(
        cls, 
        db: AsyncSession, 
        conversation_id: str, 
        user_id: str
    ) -> Conversation:
        """Create a new conversation record linked to a user account."""
        await cls.create_user_if_not_exists(db, user_id)
        
        stmt = select(Conversation).where(Conversation.conversation_id == conversation_id)
        result = await db.execute(stmt)
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            conversation = Conversation(conversation_id=conversation_id, user_id=user_id)
            db.add(conversation)
            await db.commit()
            logger.info(f"Created new conversation session: {conversation_id} (linked to user: {user_id})")
        return conversation

    @staticmethod
    async def get_recent_messages(db: AsyncSession, conversation_id: str, limit: int = 10) -> List[Message]:
        """Retrieve the most recent messages for a conversation, ordered chronologically."""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
        result = await db.execute(stmt)
        messages = result.scalars().all()
        return list(reversed(messages))

    @classmethod
    async def store_message_and_embed(
        cls, 
        db: AsyncSession, 
        conversation_id: str, 
        user_id: str, 
        role: str, 
        content: str
    ) -> Message:
        """Save message to SQL database and compute its vector embedding for user-level memory."""
        # Ensure conversation and user exist
        await cls.create_conversation_if_not_exists(db, conversation_id, user_id)

        # Create message record
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content
        )
        db.add(message)
        await db.flush() # Populate message.id

        # Generate embedding (hits LRU cache) and save to memory_embeddings
        embedding_vector = EmbedderService.encode_single(content)
        memory_entry = MemoryEmbedding(
            message_id=message.id,
            conversation_id=conversation_id,
            user_id=user_id,
            content=content,
            embedding=embedding_vector,
            meta={"role": role}
        )
        db.add(memory_entry)
        await db.commit()
        
        logger.debug(f"Stored message and user memory embedding for user {user_id}")
        return message

    @classmethod
    async def search_memory(
        cls,
        db: AsyncSession,
        user_id: str,
        query: str,
        recent_message_ids: Set[uuid.UUID],
        candidate_k: int = None,
        final_k: int = None,
        threshold: float = None
    ) -> List[Dict[str, Any]]:
        """
        Search historical conversation memory *across all conversations* of this user.
        Excludes messages that are currently in the active chat context.
        Reranks top candidates using Cross-Encoder.
        """
        if candidate_k is None:
            candidate_k = settings.TOP_K_MEMORY
        if final_k is None:
            final_k = settings.TOP_K_RERANKED
        if threshold is None:
            threshold = settings.SIMILARITY_THRESHOLD_MEMORY

        query_embedding = EmbedderService.encode_single(query)

        # Cosine distance computation
        cosine_distance = MemoryEmbedding.embedding.cosine_distance(query_embedding).label("distance")
        max_distance = 1.0 - threshold

        # Query past memories for this user
        stmt = (
            select(MemoryEmbedding, cosine_distance)
            .where(MemoryEmbedding.user_id == user_id)
            .where(MemoryEmbedding.embedding.cosine_distance(query_embedding) <= max_distance)
        )
        
        # Exclude recent messages if any
        if recent_message_ids:
            stmt = stmt.where(MemoryEmbedding.message_id.notin_(list(recent_message_ids)))

        stmt = stmt.order_by("distance").limit(candidate_k)

        result = await db.execute(stmt)
        rows = result.all()

        candidates = []
        for mem, dist in rows:
            similarity = 1.0 - dist
            candidates.append({
                "message_id": str(mem.message_id),
                "role": mem.meta.get("role", "user"),
                "content": mem.content,
                "score": float(similarity)
            })

        if not candidates:
            return []

        # Rerank memories using Cross-Encoder
        reranked_memories = RerankerService.rerank(
            query=query,
            items=candidates,
            text_extractor=lambda item: item["content"],
            top_k=final_k
        )

        return reranked_memories

    @classmethod
    async def search_episodic_memories(
        cls,
        db: AsyncSession,
        user_id: str,
        query: str,
        top_k: int = 5,
        threshold: float = None
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant episodic memory summaries for the user."""
        if threshold is None:
            threshold = settings.SIMILARITY_THRESHOLD_EPISODIC

        query_embedding = EmbedderService.encode_single(query)
        cosine_distance = UserEpisodicMemory.embedding.cosine_distance(query_embedding).label("distance")
        max_distance = 1.0 - threshold

        stmt = (
            select(UserEpisodicMemory, cosine_distance)
            .where(UserEpisodicMemory.user_id == user_id)
            .where(UserEpisodicMemory.embedding.cosine_distance(query_embedding) <= max_distance)
            .order_by("distance")
            .limit(top_k)
        )

        result = await db.execute(stmt)
        rows = result.all()

        memories = []
        for mem, dist in rows:
            similarity = 1.0 - dist
            memories.append({
                "fact": mem.fact,
                "score": float(similarity)
            })
        return memories

    @classmethod
    async def add_episodic_memory(cls, db: AsyncSession, user_id: str, fact: str):
        """
        Embed and save a fact about the user.
        Deduplicates against existing facts using high similarity (0.85).
        """
        if not fact or not fact.strip():
            return

        fact_clean = fact.strip()
        query_embedding = EmbedderService.encode_single(fact_clean)

        # Check for highly similar facts already stored for this user
        cosine_distance = UserEpisodicMemory.embedding.cosine_distance(query_embedding)
        # Similarity > 0.85 => Distance < 0.15
        stmt = (
            select(UserEpisodicMemory)
            .where(UserEpisodicMemory.user_id == user_id)
            .where(cosine_distance <= (1.0 - settings.EPISODIC_DEDUPLICATION_THRESHOLD))
            .limit(1)
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            logger.info(f"Episodic fact already exists, skipping: '{fact_clean}'")
            return

        # Insert new episodic memory
        new_memory = UserEpisodicMemory(
            user_id=user_id,
            fact=fact_clean,
            embedding=query_embedding
        )
        db.add(new_memory)
        await db.commit()
        logger.info(f"[OK] Saved new episodic fact for user {user_id}: '{fact_clean}'")

    @classmethod
    async def compress_conversation(cls, db: AsyncSession, conversation_id: str):
        """
        Check if the message count in this conversation is a multiple of 25.
        If so, invoke DeepSeek to summarize the history and save it to the conversation record.
        """
        # Count total messages in this session
        stmt_count = select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
        result_count = await db.execute(stmt_count)
        msg_count = result_count.scalar() or 0

        # Run compression every 25 messages
        if msg_count > 0 and msg_count % 25 == 0:
            logger.info(f"Message count is {msg_count}. Running conversation history compression for {conversation_id}...")
            
            # Fetch conversation model
            stmt_conv = select(Conversation).where(Conversation.conversation_id == conversation_id)
            res_conv = await db.execute(stmt_conv)
            conv = res_conv.scalar_one_or_none()
            if not conv:
                return

            # Fetch all messages in chronological order
            stmt_msgs = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
            res_msgs = await db.execute(stmt_msgs)
            messages = res_msgs.scalars().all()
            
            history_text = "\n".join([f"{msg.role.upper()}: {msg.content}" for msg in messages])
            
            system_msg = (
                "You are an expert summarization bot. Your job is to create or update a cumulative summary of the "
                "conversation between the User and the Assistant. Make sure to capture key technical DX/CMS platforms "
                "(e.g., Sitecore DXP, XM Cloud, Content Hub), company scale, business requirements, and contact data."
            )
            
            user_msg = (
                f"Existing Cumulative Summary:\n{conv.summary or 'None'}\n\n"
                f"Full Conversation Log:\n{history_text}\n\n"
                "Please output an updated, structured, cumulative summary. Keep it concise, professional, and clear. "
                "Do not include conversational filler or JSON formatting."
            )
            
            try:
                new_summary = await call_deepseek([
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg}
                ])
                conv.summary = new_summary.strip()
                await db.commit()
                logger.info(f"[OK] Conversation compression completed for {conversation_id}")
            except Exception as e:
                logger.error(f"Failed to compress conversation: {e}")
                # Don't fail the request on summary background error

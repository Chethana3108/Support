import logging
from typing import Optional, Tuple
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, Conversation, MemoryEmbedding, UserEpisodicMemory

logger = logging.getLogger("biztechbot")


class UserIdentityService:
    """
    Resolves anonymous browser sessions into real user identities using
    stable identifiers (email, phone). Handles merging when an anonymous
    user is identified as an existing known user.
    
    Flow:
        1. Anonymous visitor gets a UUID-based user_id
        2. Bot collects email or phone during conversation
        3. This service checks if that email/phone already belongs to a known user
        4. If yes → merge anonymous user's data into the known user
        5. If no  → promote the anonymous user by storing their email/phone
    """

    @classmethod
    async def find_user_by_email(cls, db: AsyncSession, email: str) -> Optional[User]:
        """Find an existing user by email address."""
        if not email or not email.strip():
            return None
        stmt = select(User).where(User.email == email.strip().lower())
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    async def find_user_by_phone(cls, db: AsyncSession, phone: str) -> Optional[User]:
        """Find an existing user by phone number."""
        if not phone or not phone.strip():
            return None
        # Normalize phone: strip spaces, dashes, but keep + prefix
        normalized = phone.strip().replace(" ", "").replace("-", "")
        stmt = select(User).where(User.phone == normalized)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    async def merge_users(
        cls, 
        db: AsyncSession, 
        source_user_id: str, 
        target_user_id: str
    ) -> None:
        """
        Merge all data from source_user into target_user.
        Reassigns conversations, memory embeddings, and episodic memories,
        then deletes the source user record.
        
        This is the core operation that unifies sessions across devices.
        """
        logger.info(
            f"Merging user {source_user_id} → {target_user_id} "
            f"(reassigning conversations, memories, episodic facts)"
        )

        # Reassign conversations
        await db.execute(
            update(Conversation)
            .where(Conversation.user_id == source_user_id)
            .values(user_id=target_user_id)
        )

        # Reassign memory embeddings
        await db.execute(
            update(MemoryEmbedding)
            .where(MemoryEmbedding.user_id == source_user_id)
            .values(user_id=target_user_id)
        )

        # Reassign episodic memories
        # (deduplication happens naturally via the 0.85 similarity threshold
        #  when new facts are added — existing facts won't cause issues)
        await db.execute(
            update(UserEpisodicMemory)
            .where(UserEpisodicMemory.user_id == source_user_id)
            .values(user_id=target_user_id)
        )

        # Delete the now-empty source user
        source_user = await db.get(User, source_user_id)
        if source_user:
            await db.delete(source_user)

        await db.commit()
        logger.info(f"User merge complete: {source_user_id} → {target_user_id}")

    @classmethod
    async def resolve_user_identity(
        cls,
        db: AsyncSession,
        current_user_id: str,
        email: Optional[str] = None,
        phone: Optional[str] = None
    ) -> Tuple[str, bool]:
        """
        Core identity resolution logic. Called whenever new contact info is collected.
        
        Args:
            db: Database session
            current_user_id: The user_id currently associated with this session
            email: Newly collected email (if any)
            phone: Newly collected phone (if any)
            
        Returns:
            Tuple of (resolved_user_id, was_merged)
            - resolved_user_id: The canonical user_id (may differ from current_user_id if merged)
            - was_merged: True if the current session was merged into an existing user
        """
        if not email and not phone:
            return current_user_id, False

        # Normalize inputs
        email_clean = email.strip().lower() if email and email.strip() else None
        phone_clean = phone.strip().replace(" ", "").replace("-", "") if phone and phone.strip() else None

        # Search for existing user by email first, then phone
        existing_user = None
        matched_by = None

        if email_clean:
            existing_user = await cls.find_user_by_email(db, email_clean)
            if existing_user:
                matched_by = "email"

        if not existing_user and phone_clean:
            existing_user = await cls.find_user_by_phone(db, phone_clean)
            if existing_user:
                matched_by = "phone"

        if existing_user and existing_user.user_id != current_user_id:
            # Found a different existing user with this email/phone → MERGE
            logger.info(
                f"Identity match found by {matched_by}: "
                f"merging anonymous user {current_user_id} → known user {existing_user.user_id}"
            )
            await cls.merge_users(db, source_user_id=current_user_id, target_user_id=existing_user.user_id)

            # Update the existing user's contact info if we have new data
            if email_clean and not existing_user.email:
                existing_user.email = email_clean
            if phone_clean and not existing_user.phone:
                existing_user.phone = phone_clean
            await db.commit()

            return existing_user.user_id, True

        elif existing_user and existing_user.user_id == current_user_id:
            # Same user — just update any missing contact fields
            updated = False
            if email_clean and not existing_user.email:
                existing_user.email = email_clean
                updated = True
            if phone_clean and not existing_user.phone:
                existing_user.phone = phone_clean
                updated = True
            if updated:
                await db.commit()
                logger.debug(f"Updated contact info for existing user {current_user_id}")
            return current_user_id, False

        else:
            # No existing user found — promote this anonymous user
            current_user = await db.get(User, current_user_id)
            if current_user:
                updated = False
                if email_clean and not current_user.email:
                    current_user.email = email_clean
                    updated = True
                if phone_clean and not current_user.phone:
                    current_user.phone = phone_clean
                    updated = True
                if updated:
                    await db.commit()
                    logger.info(
                        f"Promoted anonymous user {current_user_id} with "
                        f"email={email_clean}, phone={phone_clean}"
                    )
            return current_user_id, False

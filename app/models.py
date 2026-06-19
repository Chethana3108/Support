import uuid
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import String, DateTime, ForeignKey, Boolean, Integer, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector
from app.database import Base

def get_utc_now():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now)

    # Relationships
    conversations: Mapped[List["Conversation"]] = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    memory_embeddings: Mapped[List["MemoryEmbedding"]] = relationship("MemoryEmbedding", back_populates="user", cascade="all, delete-orphan")
    episodic_memories: Mapped[List["UserEpisodicMemory"]] = relationship("UserEpisodicMemory", back_populates="user", cascade="all, delete-orphan")


class Conversation(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    summary: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="conversations")
    messages: Mapped[List["Message"]] = relationship("Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")
    lead_state: Mapped["LeadState"] = relationship("LeadState", back_populates="conversation", cascade="all, delete-orphan", uselist=False)
    memory_embeddings: Mapped[List["MemoryEmbedding"]] = relationship("MemoryEmbedding", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now)

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
    memory_embeddings: Mapped[List["MemoryEmbedding"]] = relationship("MemoryEmbedding", back_populates="message", cascade="all, delete-orphan")


class LeadState(Base):
    __tablename__ = "lead_state"

    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.conversation_id", ondelete="CASCADE"), primary_key=True)
    lead_name: Mapped[str] = mapped_column(String, default="")
    company_name: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="")
    phone: Mapped[str] = mapped_column(String, default="")
    notes: Mapped[str] = mapped_column(String, default="")
    lead_saved: Mapped[bool] = mapped_column(Boolean, default=False)
    lead_id: Mapped[str] = mapped_column(String, nullable=True)

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="lead_state")


class MemoryEmbedding(Base):
    __tablename__ = "memory_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    # Vector of 384 dimensions matching sentence-transformers/all-MiniLM-L6-v2
    embedding: Mapped[Vector] = mapped_column(Vector(384), nullable=False)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="memory_embeddings")
    message: Mapped["Message"] = relationship("Message", back_populates="memory_embeddings")
    user: Mapped["User"] = relationship("User", back_populates="memory_embeddings")


class UserEpisodicMemory(Base):
    __tablename__ = "user_episodic_memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    fact: Mapped[str] = mapped_column(String, nullable=False)
    # Vector of 384 dimensions matching sentence-transformers/all-MiniLM-L6-v2
    embedding: Mapped[Vector] = mapped_column(Vector(384), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="episodic_memories")


class WebsiteChunk(Base):
    __tablename__ = "website_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    # Vector of 384 dimensions matching sentence-transformers/all-MiniLM-L6-v2
    embedding: Mapped[Vector] = mapped_column(Vector(384), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now)

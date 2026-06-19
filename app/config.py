import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Database Settings
    DATABASE_URL: str
    DATABASE_POOL_SIZE: int = 50
    DATABASE_MAX_OVERFLOW: int = 100
    
    # DeepSeek API Settings
    DEEPSEEK_API_KEY: str
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    
    # ERPNext Settings
    ERPNEXT_URL: str = "https://bizcentraldemo.biztechnosys.in"
    ERPNEXT_API_KEY: str
    ERPNEXT_API_SECRET: str
    ERPNEXT_SSL_VERIFY: bool = True
    
    # RAG Settings
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 100
    
    # RAG Search Tuning
    TOP_K_KNOWLEDGE: int = 20  # Fetch more candidates for reranking
    TOP_K_MEMORY: int = 20     # Fetch more candidates for reranking
    TOP_K_RERANKED: int = 5    # Select top K after cross-encoder reranking
    
    # Similarity thresholds (increased as recommended to filter out noise)
    SIMILARITY_THRESHOLD_KNOWLEDGE: float = 0.45
    SIMILARITY_THRESHOLD_MEMORY: float = 0.55
    SIMILARITY_THRESHOLD_EPISODIC: float = 0.55
    EPISODIC_DEDUPLICATION_THRESHOLD: float = 0.85
    
    # Lead Collection Turn Rules
    MINDFUL_TALK_TURNS: int = 2
    
    # Rate Limiting & API Security
    RATE_LIMIT_PER_MINUTE: int = 60
    CORS_ALLOWED_ORIGINS: str = "https://beta.biztechnosys.com"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

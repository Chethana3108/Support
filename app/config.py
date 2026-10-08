import os
from typing import List
from dotenv import load_dotenv
load_dotenv(override=True)
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
    CHUNK_SIZE: int = 2400
    CHUNK_OVERLAP: int = 400
    
    # Crawler Settings
    CRAWL_BASE_URL: str = "https://beta.biztechnosys.com/"
    CRAWL_INTERVAL_HOURS: int = 6
    CRAWL_MAX_PAGES: int = 500
    CRAWL_MAX_DEPTH: int = 10
    CRAWL_CONCURRENT_WORKERS: int = 5
    ENABLE_AUTO_CRAWLER: bool = False
    
    # RAG Search Tuning
    TOP_K_KNOWLEDGE: int = 10  # Fetch candidates for reranking (fast, high precision)
    TOP_K_MEMORY: int = 8      # Fetch candidates for reranking
    TOP_K_RERANKED: int = 4    # Select top K after cross-encoder reranking
    
    # Similarity thresholds (0.25 for high recall candidate retrieval, precision handled by cross-encoder)
    SIMILARITY_THRESHOLD_KNOWLEDGE: float = 0.25
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
    DEBUG: bool = False
    
    # Azure / Microsoft Graph Settings
    AZURE_TENANT_ID: str = "biztechnosys.com"
    AZURE_CLIENT_ID: str = "0c036f03-5c13-4313-b667-724728101e38"
    AZURE_CLIENT_SECRET: str = ""  # Set via .env file
    OUTLOOK_EMAIL: str = "chethana@biztechnosys.com"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

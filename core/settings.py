"""
Application settings loaded from environment variables.
Uses pydantic-settings for validation and .env file support.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_URL: str = (
        "postgresql+asyncpg://orchestraty:orchestraty_dev@localhost:5432/orchestraty"
    )

    # ── MinIO / S3-compatible object storage ──────────────────────────────
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET_NAME: str = "orchestraty"
    MINIO_USE_SSL: bool = False

    # ── Redis ─────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Auth / JWT ────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ── Qdrant (Vector DB) ──────────────────────────────────────────────
    QDRANT_HOST: str = "qdrant"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "orchestraty_embeddings"

    # ── Celery ───────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = "redis://redis:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/2"

    # ── Embedding Model ──────────────────────────────────────────────────
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    # ── Initial Superuser (auto-created on first startup) ──────────────────
    ADMIN_EMAIL: str = "admin@orchestraty.local"
    ADMIN_PASSWORD: str = "Admin@12345"
    ADMIN_NAME: str = "System Admin"

    # ── Runtime environment ───────────────────────────────────────────────
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"


settings = Settings()

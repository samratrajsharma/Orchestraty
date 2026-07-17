"""Central configuration for the orchestrator.

All values are overridable via ``ORCH_*`` environment variables or a ``.env``
file (see ``.env.example`` at the repo root).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORCH_", env_file=".env", extra="ignore"
    )

    app_name: str = "Orchestraty Orchestrator"
    environment: str = "development"
    log_level: str = "INFO"

    # State store — the control plane's source of truth.
    database_url: str = (
        "postgresql+asyncpg://orchestraty:orchestraty@postgres:5432/orchestraty"
    )

    # Event bus — how the engine and agents exchange task events.
    redis_url: str = "redis://redis:6379/0"


settings = Settings()

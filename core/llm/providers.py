"""
LLM provider definitions, configuration schema, and provider defaults.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class LLMProvider(str, Enum):
    """Supported LLM provider backends."""

    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GROQ = "groq"
    CUSTOM = "custom"


class LLMConfig(BaseModel):
    """Configuration for connecting to an LLM provider."""

    provider: LLMProvider
    model_name: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1)
    timeout: int = Field(default=60, ge=1)


PROVIDER_DEFAULTS: dict[LLMProvider, dict] = {
    LLMProvider.OLLAMA: {
        "base_url": "http://localhost:11434",
        "models": ["llama3", "mistral", "phi3", "gemma2", "llava"],
    },
    LLMProvider.OPENAI: {
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
    },
    LLMProvider.ANTHROPIC: {
        "base_url": "https://api.anthropic.com",
        "models": ["claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"],
    },
    LLMProvider.GROQ: {
        "base_url": "https://api.groq.com/openai/v1",
        "models": [
            "llama-3.1-70b-versatile",
            "mixtral-8x7b-32768",
            "llama-3.1-8b-instant",
        ],
    },
    LLMProvider.CUSTOM: {
        "base_url": None,
        "models": [],
    },
}

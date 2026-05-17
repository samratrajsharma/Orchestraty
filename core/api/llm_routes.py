"""
LLM Configuration API — manage provider settings, test connections,
and discover available models.
All endpoints require at minimum the 'viewer' role.
"""

from typing import Optional
from uuid import UUID

import httpx
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.middleware.rbac import require_viewer
from core.models.user import User
from core.models.llm_config import LLMConfigRecord
from core.services.auth_service import get_current_user
from core.llm.providers import LLMProvider, LLMConfig, PROVIDER_DEFAULTS
from core.llm.service import LLMService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/llm", tags=["llm"])


# ── Schemas ──────────────────────────────────────────────────────────────


class LLMConfigSave(BaseModel):
    provider: str
    model_name: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1)
    is_default: bool = False


class LLMTestRequest(BaseModel):
    provider: str
    model_name: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────


def _provider_display_name(provider: LLMProvider) -> str:
    names = {
        LLMProvider.OLLAMA: "Ollama (Local)",
        LLMProvider.OPENAI: "OpenAI",
        LLMProvider.ANTHROPIC: "Anthropic",
        LLMProvider.GROQ: "Groq",
        LLMProvider.CUSTOM: "Custom / OpenAI-compatible",
    }
    return names.get(provider, provider.value)


def _provider_requires_api_key(provider: LLMProvider) -> bool:
    return provider not in (LLMProvider.OLLAMA,)


def _resolve_provider(value: str) -> LLMProvider:
    """Convert a string to LLMProvider, raising 400 on invalid input."""
    try:
        return LLMProvider(value)
    except ValueError:
        valid = [p.value for p in LLMProvider]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider '{value}'. Must be one of: {', '.join(valid)}",
        )


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/providers")
async def list_providers(
    current_user: User = Depends(require_viewer),
):
    """Return all supported LLM providers with their defaults."""
    providers = []
    for provider, defaults in PROVIDER_DEFAULTS.items():
        providers.append(
            {
                "name": provider.value,
                "display_name": _provider_display_name(provider),
                "default_base_url": defaults.get("base_url"),
                "models": defaults.get("models", []),
                "requires_api_key": _provider_requires_api_key(provider),
            }
        )
    return {"providers": providers}


@router.get("/models/{provider}")
async def list_models(
    provider: str,
    current_user: User = Depends(require_viewer),
):
    """Return available models for a provider.

    For Ollama, attempts a live query to the local instance.
    For other providers, returns the static default list.
    """
    llm_provider = _resolve_provider(provider)
    defaults = PROVIDER_DEFAULTS.get(llm_provider, {})
    default_models: list[str] = defaults.get("models", [])

    if llm_provider == LLMProvider.OLLAMA:
        base_url = defaults.get("base_url", "http://localhost:11434")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                model_names = [m["name"] for m in data.get("models", [])]
                return {
                    "provider": provider,
                    "models": model_names,
                    "source": "live",
                    "warning": None,
                }
        except Exception as exc:
            logger.warning("Ollama unreachable at %s: %s", base_url, exc)
            return {
                "provider": provider,
                "models": default_models,
                "source": "defaults",
                "warning": f"Ollama unreachable at {base_url}. Showing default model list.",
            }

    return {
        "provider": provider,
        "models": default_models,
        "source": "defaults",
        "warning": None,
    }


@router.get("/config")
async def get_configs(
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """Return all LLM configurations for the current user."""
    result = await db.execute(
        select(LLMConfigRecord).where(LLMConfigRecord.user_id == current_user.id)
    )
    records = result.scalars().all()

    configs = [
        {
            "id": str(rec.id),
            "provider": rec.provider,
            "model_name": rec.model_name,
            "base_url": rec.base_url,
            "temperature": rec.temperature,
            "max_tokens": rec.max_tokens,
            "is_default": rec.is_default,
            "has_api_key": rec.api_key_encrypted is not None
            and rec.api_key_encrypted != "",
        }
        for rec in records
    ]
    return {"configs": configs}


@router.post("/config")
async def save_config(
    body: LLMConfigSave,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """Create or update an LLM provider configuration for the current user."""
    _resolve_provider(body.provider)  # validate provider name

    # If marking as default, clear other defaults first
    if body.is_default:
        await db.execute(
            update(LLMConfigRecord)
            .where(LLMConfigRecord.user_id == current_user.id)
            .values(is_default=False)
        )

    # Check for existing config for this user + provider
    result = await db.execute(
        select(LLMConfigRecord).where(
            LLMConfigRecord.user_id == current_user.id,
            LLMConfigRecord.provider == body.provider,
        )
    )
    existing = result.scalars().first()

    if existing:
        # Build update values
        values: dict = {
            "model_name": body.model_name,
            "base_url": body.base_url,
            "temperature": body.temperature,
            "max_tokens": body.max_tokens,
            "is_default": body.is_default,
        }
        # Only overwrite api_key if a new one was provided
        if body.api_key is not None:
            values["api_key_encrypted"] = body.api_key

        await db.execute(
            update(LLMConfigRecord)
            .where(LLMConfigRecord.id == existing.id)
            .values(**values)
        )
        await db.commit()
        return {"message": "Configuration saved", "config_id": str(existing.id)}

    # Insert new record
    new_record = LLMConfigRecord(
        user_id=current_user.id,
        provider=body.provider,
        model_name=body.model_name,
        api_key_encrypted=body.api_key,
        base_url=body.base_url,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        is_default=body.is_default,
    )
    db.add(new_record)
    await db.commit()
    await db.refresh(new_record)
    return {"message": "Configuration saved", "config_id": str(new_record.id)}


@router.delete("/config/{provider}")
async def delete_config(
    provider: str,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """Delete the current user's configuration for a specific provider."""
    _resolve_provider(provider)  # validate

    result = await db.execute(
        select(LLMConfigRecord).where(
            LLMConfigRecord.user_id == current_user.id,
            LLMConfigRecord.provider == provider,
        )
    )
    existing = result.scalars().first()
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=f"No configuration found for provider '{provider}'",
        )

    await db.execute(
        delete(LLMConfigRecord).where(LLMConfigRecord.id == existing.id)
    )
    await db.commit()
    return {"message": "Configuration deleted"}


@router.post("/test")
async def test_connection(
    body: LLMTestRequest,
    current_user: User = Depends(require_viewer),
):
    """Test connectivity to an LLM provider without persisting anything."""
    llm_provider = _resolve_provider(body.provider)
    defaults = PROVIDER_DEFAULTS.get(llm_provider, {})

    config = LLMConfig(
        provider=llm_provider,
        model_name=body.model_name,
        api_key=body.api_key,
        base_url=body.base_url or defaults.get("base_url"),
    )

    service = LLMService(config)
    result = await service.test_connection()
    return result

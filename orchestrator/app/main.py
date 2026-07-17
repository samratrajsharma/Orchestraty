"""Orchestrator API entrypoint.

System endpoints only for now. The state store is provisioned on startup (with
a connect-retry so container ordering never matters); subsequent Phase 1
features attach the task engine, event bus, agent registry, intent compiler,
and the workflow API to this same app.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from . import models  # noqa: F401  (imported so tables register on Base)
from .base import Base
from .config import settings
from .db import engine

VERSION = "0.1.0"

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("orchestrator")


async def _provision_state_store(retries: int = 30, delay: float = 2.0) -> None:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("State store not ready (attempt %d/%d): %s", attempt, retries, exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"State store unreachable after {retries} attempts: {last_err}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Orchestraty orchestrator starting (env=%s)", settings.environment)
    await _provision_state_store()
    logger.info(
        "State store ready: %d tables (%s)",
        len(Base.metadata.tables),
        ", ".join(sorted(Base.metadata.tables)),
    )
    yield
    await engine.dispose()
    logger.info("Orchestraty orchestrator shutting down")


app = FastAPI(title=settings.app_name, version=VERSION, lifespan=lifespan)


@app.get("/health", tags=["system"])
async def health() -> dict:
    """Liveness probe."""
    return {"status": "healthy", "service": "orchestrator", "version": VERSION}


@app.get("/readyz", tags=["system"])
async def readyz() -> dict:
    """Readiness - verifies the state store is reachable."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {
        "status": "ready",
        "state_store": "connected",
        "tables": sorted(Base.metadata.tables),
    }


@app.get("/", tags=["system"])
async def root() -> dict:
    return {
        "name": settings.app_name,
        "environment": settings.environment,
        "docs": "/docs",
    }

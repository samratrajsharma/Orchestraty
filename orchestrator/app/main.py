"""Orchestrator API entrypoint.

For now this exposes only system endpoints (``/`` and ``/health``). Subsequent
Phase 1 features attach the state store, task engine, event bus, agent registry,
intent compiler, and the workflow API to this same app.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .config import settings

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("orchestrator")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Orchestraty orchestrator starting (env=%s)", settings.environment)
    yield
    logger.info("Orchestraty orchestrator shutting down")


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)


@app.get("/health", tags=["system"])
async def health() -> dict:
    """Liveness probe used by docker-compose and, later, Kubernetes."""
    return {"status": "healthy", "service": "orchestrator", "version": app.version}


@app.get("/", tags=["system"])
async def root() -> dict:
    return {
        "name": settings.app_name,
        "environment": settings.environment,
        "docs": "/docs",
    }

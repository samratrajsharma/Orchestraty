"""Orchestrator API entrypoint.

On startup: provision the state store, start the agent reaper, connect the
event bus, and start the background result consumer (the reconciliation loop).
Event-bus startup is best-effort so a broker hiccup degrades dispatch rather
than crashing the API.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from sqlalchemy import text

from . import models  # noqa: F401  (imported so tables register on Base)
from .base import Base
from .bus import EventBus
from .config import settings
from .db import engine
from .dispatcher import Dispatcher
from .engine import TaskEngine
from .reaper import AgentReaper
from .registry import AgentRegistry
from .results_consumer import ResultConsumer
from .routers import agents as agents_routes

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


async def _connect_bus(bus: EventBus, retries: int = 30, delay: float = 2.0) -> None:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            await bus.connect()
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("Event bus not ready (attempt %d/%d): %s", attempt, retries, exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"Event bus unreachable after {retries} attempts: {last_err}")


async def _cancel(task) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Orchestraty orchestrator starting (env=%s)", settings.environment)
    await _provision_state_store()
    logger.info(
        "State store ready: %d tables (%s)",
        len(Base.metadata.tables),
        ", ".join(sorted(Base.metadata.tables)),
    )

    # Agent registry + reaper (DB-only, always on).
    registry = AgentRegistry()
    reaper = AgentReaper(registry)
    app.state.registry = registry
    app.state.reaper = reaper
    app.state.reaper_task = asyncio.create_task(reaper.start())

    # Event bus + dispatch + result consumer (best-effort).
    app.state.bus = None
    app.state.dispatcher = None
    app.state.result_consumer = None
    app.state.consumer_task = None
    try:
        bus = EventBus(settings.redis_url)
        await _connect_bus(bus)
        task_engine = TaskEngine()
        dispatcher = Dispatcher(bus, task_engine, registry)
        consumer = ResultConsumer(bus, task_engine, dispatcher)
        app.state.bus = bus
        app.state.task_engine = task_engine
        app.state.dispatcher = dispatcher
        app.state.result_consumer = consumer
        app.state.consumer_task = asyncio.create_task(consumer.start())
    except Exception:  # noqa: BLE001
        logger.exception("Event bus startup failed; API will run without dispatch")

    yield

    if app.state.result_consumer is not None:
        app.state.result_consumer.stop()
    if app.state.reaper is not None:
        app.state.reaper.stop()
    await _cancel(app.state.consumer_task)
    await _cancel(app.state.reaper_task)
    if app.state.bus is not None:
        await app.state.bus.close()
    await engine.dispose()
    logger.info("Orchestraty orchestrator shutting down")


app = FastAPI(title=settings.app_name, version=VERSION, lifespan=lifespan)
app.include_router(agents_routes.router)


@app.get("/health", tags=["system"])
async def health() -> dict:
    """Liveness probe."""
    return {"status": "healthy", "service": "orchestrator", "version": VERSION}


@app.get("/readyz", tags=["system"])
async def readyz(request: Request) -> dict:
    """Readiness - verifies the state store and event bus are reachable."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {
        "status": "ready",
        "state_store": "connected",
        "event_bus": "connected" if request.app.state.bus is not None else "disconnected",
        "tables": sorted(Base.metadata.tables),
    }


@app.get("/debug/bus", tags=["debug"])
async def debug_bus(request: Request) -> dict:
    """Publish + read back a message to prove the event bus works."""
    bus = request.app.state.bus
    if bus is None:
        return {"event_bus": "disconnected"}
    ok = await bus.selftest()
    return {"event_bus": "ok" if ok else "no_message"}


@app.get("/", tags=["system"])
async def root() -> dict:
    return {
        "name": settings.app_name,
        "environment": settings.environment,
        "docs": "/docs",
    }

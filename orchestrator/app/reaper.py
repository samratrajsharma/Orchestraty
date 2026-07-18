"""Background loop that marks agents OFFLINE when their heartbeats go stale."""

import asyncio
import logging

from .db import SessionLocal

logger = logging.getLogger("orchestrator.reaper")


class AgentReaper:
    def __init__(self, registry, interval: float = 15.0, ttl: float = 45.0):
        self._registry = registry
        self._interval = interval
        self._ttl = ttl
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("agent reaper started (interval=%.0fs ttl=%.0fs)", self._interval, self._ttl)
        while self._running:
            try:
                async with SessionLocal() as session:
                    reaped = await self._registry.reap_stale(session, ttl_seconds=self._ttl)
                    if reaped:
                        await session.commit()
                        logger.info("reaped %d stale agent(s)", len(reaped))
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception("reaper error; continuing")
            await asyncio.sleep(self._interval)
        logger.info("agent reaper stopped")

    def stop(self) -> None:
        self._running = False

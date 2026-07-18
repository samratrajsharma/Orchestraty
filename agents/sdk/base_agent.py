"""Reusable base for Orchestraty agents.

Subclass and implement `handle(payload) -> dict`. The base handles the whole
agent contract: register with the orchestrator, heartbeat, consume tasks from
the capability stream, and publish success/error results.
"""

import asyncio
import logging
import os

import httpx
import redis.asyncio as aioredis

from .streams import RESULTS_STREAM, decode, encode, result_envelope, task_stream


class BaseAgent:
    def __init__(self, name, capabilities, *, api_url=None, redis_url=None):
        self.name = name
        self.capabilities = capabilities
        self.capability = capabilities[0]
        self.api_url = api_url or os.getenv("ORCH_API_URL", "http://orchestrator:8000")
        self.redis_url = redis_url or os.getenv("ORCH_REDIS_URL", "redis://redis:6379/0")
        self.group = f"agent:{self.capability}"
        self._log = logging.getLogger(f"agent.{name}")

    async def handle(self, payload: dict) -> dict:
        raise NotImplementedError

    async def register(self, client: httpx.AsyncClient) -> str:
        for attempt in range(1, 31):
            try:
                r = await client.post(
                    f"{self.api_url}/agents/register",
                    json={"name": self.name, "capabilities": self.capabilities, "meta": {}},
                )
                r.raise_for_status()
                agent_id = r.json()["id"]
                self._log.info("registered as %s (%s)", self.name, agent_id)
                return agent_id
            except Exception as exc:  # noqa: BLE001
                self._log.warning("register failed (%d/30): %s", attempt, exc)
                await asyncio.sleep(2)
        raise RuntimeError("could not register with orchestrator")

    async def _heartbeat(self, client: httpx.AsyncClient, agent_id: str) -> None:
        while True:
            try:
                await client.post(f"{self.api_url}/agents/{agent_id}/heartbeat")
            except Exception as exc:  # noqa: BLE001
                self._log.warning("heartbeat failed: %s", exc)
            await asyncio.sleep(15)

    async def _ensure_group(self, r: aioredis.Redis) -> None:
        try:
            await r.xgroup_create(task_stream(self.capability), self.group, id="0", mkstream=True)
        except Exception as exc:  # noqa: BLE001
            if "BUSYGROUP" not in str(exc):
                raise

    async def _consume(self, r: aioredis.Redis) -> None:
        stream = task_stream(self.capability)
        await self._ensure_group(r)
        self._log.info("consuming %s as group %s", stream, self.group)
        while True:
            resp = await r.xreadgroup(self.group, self.name, {stream: ">"}, count=10, block=5000)
            for _stream, entries in resp or []:
                for msg_id, fields in entries:
                    data = decode(fields)
                    task_id = data.get("task_id")
                    try:
                        result = await self.handle(data.get("payload", {}))
                        await r.xadd(RESULTS_STREAM, encode(result_envelope(task_id, True, result=result)))
                        self._log.info("task %s -> success", task_id)
                    except Exception as exc:  # noqa: BLE001
                        await r.xadd(RESULTS_STREAM, encode(result_envelope(task_id, False, error=str(exc))))
                        self._log.warning("task %s -> failed: %s", task_id, exc)
                    await r.xack(stream, self.group, msg_id)

    async def run(self) -> None:
        r = aioredis.from_url(self.redis_url, decode_responses=True)
        async with httpx.AsyncClient(timeout=10.0) as client:
            agent_id = await self.register(client)
            hb = asyncio.create_task(self._heartbeat(client, agent_id))
            try:
                await self._consume(r)
            finally:
                hb.cancel()
                await r.aclose()

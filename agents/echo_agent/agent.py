"""Reference "echo" agent.

Demonstrates the full agent contract:
  1. register with the orchestrator (capability: echo) and heartbeat
  2. consume tasks from its capability stream (orch:tasks:echo)
  3. do the work (echo the payload) and publish a result to orch:results

Self-contained: it re-declares the small stream conventions so it needs no
orchestrator code.
"""

import asyncio
import json
import logging
import os

import httpx
import redis.asyncio as aioredis

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("echo-agent")

API = os.getenv("ORCH_API_URL", "http://orchestrator:8000")
REDIS_URL = os.getenv("ORCH_REDIS_URL", "redis://redis:6379/0")
NAME = os.getenv("AGENT_NAME", "echo-agent-1")
CAPABILITY = "echo"

TASKS_STREAM = f"orch:tasks:{CAPABILITY}"
RESULTS_STREAM = "orch:results"
GROUP = f"agent:{CAPABILITY}"
DATA = "data"


def encode(message: dict) -> dict:
    return {DATA: json.dumps(message)}


def decode(fields) -> dict:
    raw = fields.get(DATA) if isinstance(fields, dict) else None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw) if raw else {}


async def register(client: httpx.AsyncClient) -> str:
    for attempt in range(1, 31):
        try:
            r = await client.post(
                f"{API}/agents/register",
                json={"name": NAME, "capabilities": [CAPABILITY], "meta": {"kind": "reference"}},
            )
            r.raise_for_status()
            agent_id = r.json()["id"]
            log.info("registered as %s (%s)", NAME, agent_id)
            return agent_id
        except Exception as exc:  # noqa: BLE001
            log.warning("register failed (%d/30): %s", attempt, exc)
            await asyncio.sleep(2)
    raise RuntimeError("could not register with orchestrator")


async def heartbeat_loop(client: httpx.AsyncClient, agent_id: str) -> None:
    while True:
        try:
            await client.post(f"{API}/agents/{agent_id}/heartbeat")
        except Exception as exc:  # noqa: BLE001
            log.warning("heartbeat failed: %s", exc)
        await asyncio.sleep(15)


async def ensure_group(r: aioredis.Redis) -> None:
    try:
        await r.xgroup_create(TASKS_STREAM, GROUP, id="0", mkstream=True)
    except Exception as exc:  # noqa: BLE001
        if "BUSYGROUP" not in str(exc):
            raise


async def do_work(payload: dict) -> dict:
    """The 'echo' capability: return the message it was given."""
    return {"echoed": payload.get("message", payload)}


async def consume(r: aioredis.Redis) -> None:
    await ensure_group(r)
    log.info("consuming %s as group %s", TASKS_STREAM, GROUP)
    while True:
        resp = await r.xreadgroup(GROUP, NAME, {TASKS_STREAM: ">"}, count=10, block=5000)
        for _stream, entries in resp or []:
            for msg_id, fields in entries:
                data = decode(fields)
                task_id = data.get("task_id")
                try:
                    result = await do_work(data.get("payload", {}))
                    await r.xadd(RESULTS_STREAM, encode(
                        {"task_id": task_id, "success": True, "result": result}))
                    log.info("task %s -> success", task_id)
                except Exception as exc:  # noqa: BLE001
                    await r.xadd(RESULTS_STREAM, encode(
                        {"task_id": task_id, "success": False, "error": str(exc)}))
                    log.warning("task %s -> failed: %s", task_id, exc)
                await r.xack(TASKS_STREAM, GROUP, msg_id)


async def main() -> None:
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    async with httpx.AsyncClient(timeout=10.0) as client:
        agent_id = await register(client)
        hb = asyncio.create_task(heartbeat_loop(client, agent_id))
        try:
            await consume(r)
        finally:
            hb.cancel()
            await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())

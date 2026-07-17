"""Redis Streams event bus.

Decouples the orchestrator from agents: the orchestrator publishes task
assignments to per-capability streams; agents consume them, do the work, and
publish results to a single results stream that the orchestrator consumes.
Consumer groups give at-least-once delivery with explicit acknowledgement.
"""

import logging

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from .streams import (
    RESULTS_STREAM,
    SELFTEST_STREAM,
    decode,
    encode,
    task_stream,
)

logger = logging.getLogger("orchestrator.bus")


class EventBus:
    def __init__(self, url: str):
        self._url = url
        self._redis = None

    async def connect(self) -> None:
        self._redis = aioredis.from_url(self._url, decode_responses=True)
        await self._redis.ping()
        logger.info("event bus connected")

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    @property
    def redis(self):
        if self._redis is None:
            raise RuntimeError("event bus not connected")
        return self._redis

    async def ensure_group(self, stream: str, group: str) -> None:
        try:
            await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def publish_task(self, capability: str, message: dict) -> str:
        return await self.redis.xadd(task_stream(capability), encode(message))

    async def publish_result(self, message: dict) -> str:
        return await self.redis.xadd(RESULTS_STREAM, encode(message))

    async def read(self, stream, group, consumer, count=10, block=5000):
        resp = await self.redis.xreadgroup(
            group, consumer, {stream: ">"}, count=count, block=block
        )
        out = []
        for _stream, entries in resp or []:
            for msg_id, fields in entries:
                out.append((msg_id, decode(fields)))
        return out

    async def ack(self, stream, group, msg_id) -> None:
        await self.redis.xack(stream, group, msg_id)

    async def selftest(self) -> bool:
        """Publish + read back on a scratch stream to prove connectivity."""
        await self.ensure_group(SELFTEST_STREAM, "selftest")
        await self.redis.xadd(SELFTEST_STREAM, encode({"ping": "pong"}))
        msgs = await self.read(SELFTEST_STREAM, "selftest", "selftester", count=1, block=1000)
        for msg_id, _ in msgs:
            await self.ack(SELFTEST_STREAM, "selftest", msg_id)
        return len(msgs) >= 1

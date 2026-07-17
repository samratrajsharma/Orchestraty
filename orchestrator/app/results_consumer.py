"""Background loop that consumes agent results and drives the control loop.

For each result: apply it via the task engine (succeed / retry / fail), then
dispatch any newly-unblocked tasks and reconcile the workflow status.
"""

import asyncio
import logging

from .bus import EventBus
from .db import SessionLocal
from .dispatcher import Dispatcher
from .engine import TaskEngine
from .models import Task, Workflow
from .streams import RESULTS_GROUP, RESULTS_STREAM

logger = logging.getLogger("orchestrator.results")


class ResultConsumer:
    def __init__(self, bus: EventBus, engine: TaskEngine, dispatcher: Dispatcher,
                 consumer_name: str = "orchestrator-1"):
        self._bus = bus
        self._engine = engine
        self._dispatcher = dispatcher
        self._consumer = consumer_name
        self._running = False

    async def start(self) -> None:
        await self._bus.ensure_group(RESULTS_STREAM, RESULTS_GROUP)
        self._running = True
        logger.info("result consumer started")
        while self._running:
            try:
                msgs = await self._bus.read(
                    RESULTS_STREAM, RESULTS_GROUP, self._consumer, count=10, block=5000
                )
                for msg_id, data in msgs:
                    await self._apply(data)
                    await self._bus.ack(RESULTS_STREAM, RESULTS_GROUP, msg_id)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception("result consumer error; continuing")
                await asyncio.sleep(1)
        logger.info("result consumer stopped")

    def stop(self) -> None:
        self._running = False

    async def _apply(self, data: dict) -> None:
        task_id = data.get("task_id")
        if not task_id:
            return
        async with SessionLocal() as session:
            task = await session.get(Task, task_id)
            if task is None:
                logger.warning("result for unknown task %s", task_id)
                return
            await self._engine.handle_result(
                session,
                task,
                success=bool(data.get("success")),
                result=data.get("result"),
                error=data.get("error"),
            )
            await self._dispatcher.dispatch_ready(session, task.workflow_id)
            workflow = await session.get(Workflow, task.workflow_id)
            if workflow is not None:
                await self._engine.reconcile_workflow(session, workflow)
            await session.commit()

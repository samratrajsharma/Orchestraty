"""Publishes ready tasks to the event bus and marks them ASSIGNED."""

import logging

from .bus import EventBus
from .engine import TaskEngine
from .enums import TaskStatus

logger = logging.getLogger("orchestrator.dispatcher")


class Dispatcher:
    def __init__(self, bus: EventBus, engine: TaskEngine):
        self._bus = bus
        self._engine = engine

    async def dispatch_ready(self, session, workflow_id) -> int:
        """Assign and publish every task whose dependencies are satisfied."""
        ready = await self._engine.ready_tasks(session, workflow_id)
        for task in ready:
            await self._engine.transition(session, task, TaskStatus.ASSIGNED)
            await self._bus.publish_task(
                task.capability,
                {
                    "task_id": task.id,
                    "workflow_id": task.workflow_id,
                    "name": task.name,
                    "capability": task.capability,
                    "payload": task.payload,
                },
            )
            logger.info("dispatched task %s -> %s", task.id, task.capability)
        return len(ready)

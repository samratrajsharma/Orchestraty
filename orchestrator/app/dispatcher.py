"""Publishes ready tasks to the event bus and marks them ASSIGNED.

Registry-aware: if a capability has no online agent, it warns but still
publishes (an agent may connect and consume the backlog later).
"""

import logging

from .bus import EventBus
from .engine import TaskEngine
from .enums import TaskStatus

logger = logging.getLogger("orchestrator.dispatcher")


class Dispatcher:
    def __init__(self, bus: EventBus, engine: TaskEngine, registry=None):
        self._bus = bus
        self._engine = engine
        self._registry = registry

    async def dispatch_ready(self, session, workflow_id) -> int:
        ready = await self._engine.ready_tasks(session, workflow_id)
        for task in ready:
            if self._registry is not None:
                agents = await self._registry.find_for_capability(session, task.capability)
                if not agents:
                    logger.warning(
                        "no online agent for capability '%s' (task %s); publishing anyway",
                        task.capability, task.id,
                    )
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

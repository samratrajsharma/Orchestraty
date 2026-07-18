"""Persists a compiled workflow spec as Workflow + Task rows."""

import logging

from .enums import EventType
from .intent import compile_spec, get_template
from .models import Event, Task, Workflow

logger = logging.getLogger("orchestrator.compiler")


class IntentCompiler:
    async def create_from_spec(self, session, spec: dict) -> Workflow:
        compiled = compile_spec(spec)
        workflow = Workflow(
            name=compiled["name"], intent=compiled["intent"], spec=compiled["spec"]
        )
        session.add(workflow)
        await session.flush()
        for t in compiled["tasks"]:
            session.add(
                Task(
                    id=t["id"],
                    workflow_id=workflow.id,
                    name=t["name"],
                    capability=t["capability"],
                    payload=t["payload"],
                    depends_on=t["depends_on"],
                    max_attempts=t["max_attempts"],
                    timeout_seconds=t["timeout_seconds"],
                )
            )
        session.add(
            Event(
                type=EventType.WORKFLOW_CREATED.value,
                workflow_id=workflow.id,
                payload={"name": workflow.name, "tasks": len(compiled["tasks"])},
            )
        )
        logger.info("workflow %s compiled: %d tasks", workflow.id, len(compiled["tasks"]))
        return workflow

    async def create_from_template(self, session, template_name: str) -> Workflow:
        return await self.create_from_spec(session, get_template(template_name))

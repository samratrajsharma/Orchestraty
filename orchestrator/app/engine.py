"""Task engine: applies the state machine, DAG readiness, retry policy, and
workflow reconciliation against the database.

Later features (event bus, agent registry, API) drive these methods; the pure
reasoning lives in statemachine.py / dag.py / retry.py.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import dag, retry, statemachine
from .enums import EventType, TaskStatus, WorkflowStatus
from .models import Event, Task

logger = logging.getLogger("orchestrator.engine")

_EVENT_FOR = {
    TaskStatus.QUEUED: EventType.TASK_QUEUED,
    TaskStatus.ASSIGNED: EventType.TASK_ASSIGNED,
    TaskStatus.RUNNING: EventType.TASK_STARTED,
    TaskStatus.SUCCESS: EventType.TASK_SUCCEEDED,
    TaskStatus.FAILED: EventType.TASK_FAILED,
    TaskStatus.TIMED_OUT: EventType.TASK_FAILED,
    TaskStatus.CANCELLED: EventType.TASK_FAILED,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _view(t: Task) -> dict:
    return {"id": t.id, "depends_on": list(t.depends_on or []), "status": t.status}


def _emit(session: AsyncSession, type_: EventType, *, payload=None, **cols) -> None:
    session.add(Event(type=type_.value, payload=payload or {}, **cols))


class TaskEngine:
    """Stateless service operating on a caller-provided AsyncSession."""

    async def transition(self, session, task, target, *, result=None, error=None, agent_id=None):
        target = TaskStatus(target)
        if not statemachine.can_transition(task.status, target):
            raise ValueError(
                f"illegal transition {task.status} -> {target.value} for task {task.id}"
            )
        task.status = target.value
        if target is TaskStatus.RUNNING and task.started_at is None:
            task.started_at = _now()
        if target in (TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.TIMED_OUT, TaskStatus.CANCELLED):
            task.finished_at = _now()
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error
        if agent_id is not None:
            task.assigned_agent_id = agent_id
        _emit(
            session,
            _EVENT_FOR[target],
            workflow_id=task.workflow_id,
            task_id=task.id,
            agent_id=task.assigned_agent_id,
            payload={"status": target.value},
        )
        logger.info("task %s -> %s", task.id, target.value)

    async def handle_result(self, session, task, *, success, result=None, error=None):
        """Apply an agent's result: succeed, or retry / fail per policy."""
        if success:
            await self.transition(session, task, TaskStatus.SUCCESS, result=result)
            return
        if retry.should_retry(task.attempts + 1, task.max_attempts):
            task.attempts += 1
            delay = retry.backoff_delay(task.attempts)
            await self.transition(session, task, TaskStatus.QUEUED, error=error)
            task.started_at = None
            task.finished_at = None
            _emit(
                session,
                EventType.TASK_RETRYING,
                workflow_id=task.workflow_id,
                task_id=task.id,
                payload={"attempt": task.attempts, "backoff_seconds": delay, "error": error},
            )
            logger.info("task %s retry %d/%d in %.0fs", task.id, task.attempts, task.max_attempts, delay)
        else:
            await self.transition(session, task, TaskStatus.FAILED, error=error)

    async def ready_tasks(self, session, workflow_id):
        """Tasks eligible to run right now (deps satisfied)."""
        rows = (
            await session.execute(select(Task).where(Task.workflow_id == workflow_id))
        ).scalars().all()
        ready = set(dag.ready_task_ids([_view(t) for t in rows]))
        return [t for t in rows if t.id in ready]

    async def reconcile_workflow(self, session, workflow):
        """Recompute and persist a workflow's status from its tasks."""
        rows = (
            await session.execute(select(Task).where(Task.workflow_id == workflow.id))
        ).scalars().all()
        new = statemachine.workflow_status([t.status for t in rows]).value
        if new != workflow.status:
            workflow.status = new
            if new == WorkflowStatus.SUCCEEDED.value:
                _emit(session, EventType.WORKFLOW_COMPLETED, workflow_id=workflow.id)
            elif new == WorkflowStatus.FAILED.value:
                _emit(session, EventType.WORKFLOW_FAILED, workflow_id=workflow.id)
        return workflow.status

"""Task state machine and workflow-status derivation (pure logic, no database).

Given statuses, decide which transitions are legal and what the overall
workflow status is. The engine persists; this module only reasons.
"""

from .enums import TaskStatus, WorkflowStatus

# Legal task transitions.
_ALLOWED = {
    TaskStatus.QUEUED: {TaskStatus.ASSIGNED, TaskStatus.CANCELLED},
    TaskStatus.ASSIGNED: {
        TaskStatus.RUNNING,
        TaskStatus.QUEUED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.TIMED_OUT,
        TaskStatus.QUEUED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.FAILED: {TaskStatus.QUEUED},
    TaskStatus.TIMED_OUT: {TaskStatus.QUEUED},
    TaskStatus.SUCCESS: set(),
    TaskStatus.CANCELLED: set(),
}

TERMINAL = {TaskStatus.SUCCESS, TaskStatus.CANCELLED}
FAILURE = {TaskStatus.FAILED, TaskStatus.TIMED_OUT}


def can_transition(current, target) -> bool:
    return TaskStatus(target) in _ALLOWED.get(TaskStatus(current), set())


def is_terminal(status) -> bool:
    return TaskStatus(status) in TERMINAL


def workflow_status(task_statuses):
    """Derive a WorkflowStatus from the statuses of a workflow's tasks."""
    statuses = [TaskStatus(s) for s in task_statuses]
    if not statuses:
        return WorkflowStatus.PENDING
    if all(s == TaskStatus.SUCCESS for s in statuses):
        return WorkflowStatus.SUCCEEDED
    if any(s in FAILURE for s in statuses):
        return WorkflowStatus.FAILED
    if any(
        s in (TaskStatus.RUNNING, TaskStatus.ASSIGNED, TaskStatus.QUEUED)
        for s in statuses
    ):
        return WorkflowStatus.RUNNING
    return WorkflowStatus.PENDING

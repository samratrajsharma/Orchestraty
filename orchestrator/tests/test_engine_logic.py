"""Unit tests for the pure task-engine logic (no database required)."""

import pytest

from app import dag, retry, statemachine
from app.enums import TaskStatus, WorkflowStatus


def test_transitions():
    assert statemachine.can_transition(TaskStatus.QUEUED, TaskStatus.ASSIGNED)
    assert statemachine.can_transition(TaskStatus.RUNNING, TaskStatus.SUCCESS)
    assert statemachine.can_transition(TaskStatus.FAILED, TaskStatus.QUEUED)
    assert not statemachine.can_transition(TaskStatus.SUCCESS, TaskStatus.RUNNING)
    assert not statemachine.can_transition(TaskStatus.QUEUED, TaskStatus.SUCCESS)


def test_ready_tasks():
    tasks = [
        {"id": "a", "depends_on": [], "status": "SUCCESS"},
        {"id": "b", "depends_on": ["a"], "status": "QUEUED"},
        {"id": "c", "depends_on": ["a", "b"], "status": "QUEUED"},
    ]
    assert dag.ready_task_ids(tasks) == ["b"]


def test_validate_dag_cycle():
    tasks = [
        {"id": "a", "depends_on": ["b"], "status": "QUEUED"},
        {"id": "b", "depends_on": ["a"], "status": "QUEUED"},
    ]
    with pytest.raises(ValueError):
        dag.validate_dag(tasks)


def test_validate_dag_missing_dep():
    with pytest.raises(ValueError):
        dag.validate_dag([{"id": "a", "depends_on": ["ghost"], "status": "QUEUED"}])


def test_topological_order():
    tasks = [
        {"id": "a", "depends_on": [], "status": "QUEUED"},
        {"id": "b", "depends_on": ["a"], "status": "QUEUED"},
        {"id": "c", "depends_on": ["b"], "status": "QUEUED"},
    ]
    assert dag.topological_order(tasks) == ["a", "b", "c"]


def test_backoff_and_retry():
    assert retry.should_retry(1, 3)
    assert not retry.should_retry(3, 3)
    assert retry.backoff_delay(1) == 2.0
    assert retry.backoff_delay(2) == 4.0
    assert retry.backoff_delay(100) == 60.0


def test_workflow_status():
    assert statemachine.workflow_status(["SUCCESS", "SUCCESS"]) == WorkflowStatus.SUCCEEDED
    assert statemachine.workflow_status(["SUCCESS", "FAILED"]) == WorkflowStatus.FAILED
    assert statemachine.workflow_status(["RUNNING", "QUEUED"]) == WorkflowStatus.RUNNING
    assert statemachine.workflow_status([]) == WorkflowStatus.PENDING

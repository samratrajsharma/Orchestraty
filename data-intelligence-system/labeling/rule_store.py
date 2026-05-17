"""
Rule Store — persist and retrieve labeling rule sets.

Uses the BackgroundTask model with task_type="rule_set_definition" to store
rule-set definitions in the ``result`` JSONB column.  Provides both synchronous
helpers (for Celery workers) and async helpers (for the FastAPI layer).
"""

import uuid
import logging
from datetime import datetime

from sqlalchemy import select

from core.models.background_task import BackgroundTask

logger = logging.getLogger(__name__)

TASK_TYPE = "rule_set_definition"


# ── Synchronous helpers (Celery / SyncSessionLocal) ─────────────────────

def save_rule_set(
    db,
    name: str,
    description: str,
    rules: list[dict],
    created_by: str | None = None,
) -> dict:
    """Persist a named rule set.

    Args:
        db: A synchronous SQLAlchemy ``Session``.
        name: Human-readable name for the rule set.
        description: Short description of what the rules do.
        rules: List of rule dicts (column, operator, value, label, priority).
        created_by: Optional user UUID string.

    Returns:
        ``{"id": "<uuid>", "name": "<name>"}``
    """
    task_id = str(uuid.uuid4())
    record = BackgroundTask(
        id=uuid.UUID(task_id),
        celery_task_id=f"ruleset-{task_id}",
        task_type=TASK_TYPE,
        status="completed",
        progress=1.0,
        progress_message=description,
        dataset_id=_nil_uuid(),
        created_by=uuid.UUID(created_by) if created_by else None,
        parameters={"name": name, "description": description},
        result={"name": name, "description": description, "rules": rules},
        created_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    logger.info("Saved rule set '%s' with id %s (%d rules).", name, record.id, len(rules))
    return {"id": str(record.id), "name": name}


def load_rule_set(db, rule_set_id: str) -> list[dict]:
    """Load rules from a previously saved rule set.

    Args:
        db: A synchronous SQLAlchemy ``Session``.
        rule_set_id: UUID of the BackgroundTask row.

    Returns:
        List of rule dicts stored in the ``result`` JSONB field.

    Raises:
        ValueError: If the record is not found or has no rules.
    """
    record = db.get(BackgroundTask, uuid.UUID(rule_set_id))
    if record is None or record.task_type != TASK_TYPE:
        raise ValueError(f"Rule set '{rule_set_id}' not found.")
    rules = (record.result or {}).get("rules", [])
    logger.info("Loaded rule set '%s': %d rules.", rule_set_id, len(rules))
    return rules


def list_rule_sets(db) -> list[dict]:
    """List all saved rule sets.

    Args:
        db: A synchronous SQLAlchemy ``Session``.

    Returns:
        List of dicts with ``id``, ``name``, ``description``, ``created_at``,
        and ``rule_count``.
    """
    stmt = (
        select(BackgroundTask)
        .where(BackgroundTask.task_type == TASK_TYPE)
        .order_by(BackgroundTask.created_at.desc())
    )
    records = db.execute(stmt).scalars().all()
    results = []
    for rec in records:
        params = rec.parameters or {}
        result_data = rec.result or {}
        results.append({
            "id": str(rec.id),
            "name": params.get("name", ""),
            "description": params.get("description", ""),
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
            "rule_count": len(result_data.get("rules", [])),
        })
    logger.info("Listed %d rule sets.", len(results))
    return results


# ── Async helpers (FastAPI / async session) ─────────────────────────────

async def async_save_rule_set(
    db,
    name: str,
    description: str,
    rules: list[dict],
    created_by: str | None = None,
) -> dict:
    """Async version of :func:`save_rule_set`.

    Args:
        db: An async SQLAlchemy ``AsyncSession``.
        name: Human-readable name.
        description: Short description.
        rules: List of rule definition dicts.
        created_by: Optional user UUID string.

    Returns:
        ``{"id": "<uuid>", "name": "<name>"}``
    """
    task_id = str(uuid.uuid4())
    record = BackgroundTask(
        id=uuid.UUID(task_id),
        celery_task_id=f"ruleset-{task_id}",
        task_type=TASK_TYPE,
        status="completed",
        progress=1.0,
        progress_message=description,
        dataset_id=_nil_uuid(),
        created_by=uuid.UUID(created_by) if created_by else None,
        parameters={"name": name, "description": description},
        result={"name": name, "description": description, "rules": rules},
        created_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    logger.info("Async saved rule set '%s' with id %s (%d rules).", name, record.id, len(rules))
    return {"id": str(record.id), "name": name}


async def async_list_rule_sets(db) -> list[dict]:
    """Async version of :func:`list_rule_sets`.

    Args:
        db: An async SQLAlchemy ``AsyncSession``.

    Returns:
        List of rule-set summary dicts.
    """
    stmt = (
        select(BackgroundTask)
        .where(BackgroundTask.task_type == TASK_TYPE)
        .order_by(BackgroundTask.created_at.desc())
    )
    result = await db.execute(stmt)
    records = result.scalars().all()
    results = []
    for rec in records:
        params = rec.parameters or {}
        result_data = rec.result or {}
        results.append({
            "id": str(rec.id),
            "name": params.get("name", ""),
            "description": params.get("description", ""),
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
            "rule_count": len(result_data.get("rules", [])),
        })
    logger.info("Async listed %d rule sets.", len(results))
    return results


# ── Internal helpers ────────────────────────────────────────────────────

def _nil_uuid() -> uuid.UUID:
    """Return the nil UUID (used as a placeholder dataset_id for rule sets)."""
    return uuid.UUID("00000000-0000-0000-0000-000000000000")

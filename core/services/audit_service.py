"""
Audit logging service — records and queries user actions.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.audit import AuditLog


async def log_action(
    db: AsyncSession,
    user_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Persist an audit-log entry and return it."""
    entry = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        ip_address=ip_address,
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    return entry


async def get_audit_logs(
    db: AsyncSession,
    filters: dict,
    skip: int = 0,
    limit: int = 50,
) -> list[AuditLog]:
    """Return paginated audit logs matching the supplied *filters*.

    Supported filter keys
    ---------------------
    - ``user_id``       — exact UUID match
    - ``action``        — exact string match
    - ``resource_type`` — exact string match
    - ``start_date``    — ``datetime``; inclusive lower bound on ``timestamp``
    - ``end_date``      — ``datetime``; inclusive upper bound on ``timestamp``
    """
    stmt = select(AuditLog)

    if user_id := filters.get("user_id"):
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action := filters.get("action"):
        stmt = stmt.where(AuditLog.action == action)
    if resource_type := filters.get("resource_type"):
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if start_date := filters.get("start_date"):
        stmt = stmt.where(AuditLog.timestamp >= start_date)
    if end_date := filters.get("end_date"):
        stmt = stmt.where(AuditLog.timestamp <= end_date)

    stmt = stmt.order_by(AuditLog.timestamp.desc()).offset(skip).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())

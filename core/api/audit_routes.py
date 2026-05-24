"""
Audit-log routes — query the platform-wide action log (admin only).
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.middleware.rbac import require_admin
from core.models.user import User
from core.services.audit_service import get_audit_logs

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"])

# ── Pydantic schemas ─────────────────────────────────────────────────────


class AuditLogResponse(BaseModel):
    id: UUID
    user_id: UUID | None
    action: str
    resource_type: str
    resource_id: str
    details: dict | None
    timestamp: datetime

    model_config = {"from_attributes": True}


# ── Routes ────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[AuditLogResponse],
    dependencies=[Depends(require_admin)],
)
async def list_audit_logs(
    db: AsyncSession = Depends(get_db),
    user_id: UUID | None = Query(None, description="Filter by user UUID"),
    action: str | None = Query(None, description="Filter by action name"),
    resource_type: str | None = Query(None, description="Filter by resource type"),
    start_date: datetime | None = Query(None, description="Inclusive start of date range"),
    end_date: datetime | None = Query(None, description="Inclusive end of date range"),
    skip: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(50, ge=1, le=500, description="Page size"),
):
    """Return a paginated list of audit-log entries.

    Requires the **admin** role.
    """
    filters: dict = {}
    if user_id is not None:
        filters["user_id"] = user_id
    if action is not None:
        filters["action"] = action
    if resource_type is not None:
        filters["resource_type"] = resource_type
    if start_date is not None:
        filters["start_date"] = start_date
    if end_date is not None:
        filters["end_date"] = end_date

    logs = await get_audit_logs(db=db, filters=filters, skip=skip, limit=limit)
    return [AuditLogResponse.model_validate(log) for log in logs]

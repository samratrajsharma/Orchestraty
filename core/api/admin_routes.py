"""
Admin API — user management, system stats, and configuration.
All endpoints require the 'admin' role.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.middleware.rbac import require_admin
from core.models.user import User
from core.models.background_task import BackgroundTask
from core.models.dataset import Dataset
from core.services.auth_service import hash_password
from core.services.audit_service import log_action

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ── Schemas ──────────────────────────────────────────────────────────────


class UserListItem(BaseModel):
    id: UUID
    email: str
    full_name: Optional[str]
    role: str
    is_active: bool
    created_at: str

    model_config = {"from_attributes": True}


class RoleUpdateRequest(BaseModel):
    role: str  # admin, reviewer, annotator, viewer


class UserStatusRequest(BaseModel):
    is_active: bool


class SystemStats(BaseModel):
    total_users: int
    active_users: int
    admin_count: int
    total_datasets: int
    total_tasks: int
    completed_tasks: int
    failed_tasks: int


# ── User Management ──────────────────────────────────────────────────────


@router.get("/users", response_model=list[UserListItem])
async def list_users(
    role: Optional[str] = Query(None, description="Filter by role"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all users. Admin only."""
    stmt = select(User).order_by(User.created_at.desc())

    if role:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)

    result = await db.execute(stmt)
    users = result.scalars().all()

    return [
        UserListItem(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=u.role,
            is_active=u.is_active,
            created_at=u.created_at.isoformat() if u.created_at else "",
        )
        for u in users
    ]


@router.put("/users/{user_id}/role")
async def update_user_role(
    user_id: UUID,
    body: RoleUpdateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Change a user's role. Admin only."""
    valid_roles = {"admin", "reviewer", "annotator", "viewer"}
    if body.role not in valid_roles:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{body.role}'. Must be one of: {', '.join(sorted(valid_roles))}",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent the last admin from demoting themselves
    if user.id == current_user.id and body.role != "admin":
        admin_count_result = await db.execute(
            select(func.count()).where(User.role == "admin", User.is_active == True)
        )
        admin_count = admin_count_result.scalar()
        if admin_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot demote the only admin. Promote another user first.",
            )

    old_role = user.role
    await db.execute(
        sql_update(User).where(User.id == user_id).values(role=body.role)
    )
    await db.commit()

    await log_action(
        db,
        user_id=current_user.id,
        action="admin.role_change",
        resource_type="user",
        resource_id=str(user_id),
        details={"old_role": old_role, "new_role": body.role, "target_email": user.email},
    )

    return {
        "message": f"Role updated: {old_role} → {body.role}",
        "user_id": str(user_id),
        "email": user.email,
        "new_role": body.role,
    }


@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: UUID,
    body: UserStatusRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Activate or deactivate a user. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent admin from deactivating themselves
    if user.id == current_user.id and not body.is_active:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    await db.execute(
        sql_update(User).where(User.id == user_id).values(is_active=body.is_active)
    )
    await db.commit()

    action = "admin.activate_user" if body.is_active else "admin.deactivate_user"
    await log_action(
        db,
        user_id=current_user.id,
        action=action,
        resource_type="user",
        resource_id=str(user_id),
        details={"target_email": user.email},
    )

    status_text = "activated" if body.is_active else "deactivated"
    return {"message": f"User {status_text}", "email": user.email}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a user account. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    email = user.email
    await db.delete(user)
    await db.commit()

    await log_action(
        db,
        user_id=current_user.id,
        action="admin.delete_user",
        resource_type="user",
        resource_id=str(user_id),
        details={"deleted_email": email},
    )

    return {"message": f"User '{email}' deleted permanently"}


# ── System Stats ─────────────────────────────────────────────────────────


@router.get("/stats", response_model=SystemStats)
async def get_system_stats(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get system-wide statistics. Admin only."""
    total_users = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
    active_users = (await db.execute(
        select(func.count()).where(User.is_active == True)
    )).scalar() or 0
    admin_count = (await db.execute(
        select(func.count()).where(User.role == "admin")
    )).scalar() or 0

    total_datasets = (await db.execute(select(func.count()).select_from(Dataset))).scalar() or 0

    total_tasks = (await db.execute(select(func.count()).select_from(BackgroundTask))).scalar() or 0
    completed_tasks = (await db.execute(
        select(func.count()).where(BackgroundTask.status == "completed")
    )).scalar() or 0
    failed_tasks = (await db.execute(
        select(func.count()).where(BackgroundTask.status == "failed")
    )).scalar() or 0

    return SystemStats(
        total_users=total_users,
        active_users=active_users,
        admin_count=admin_count,
        total_datasets=total_datasets,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        failed_tasks=failed_tasks,
    )

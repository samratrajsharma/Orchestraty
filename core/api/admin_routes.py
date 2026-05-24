"""
Admin API — user management, system stats, and configuration.

Permission hierarchy:
  SuperAdmin  – can promote/demote admins, delete users, reset passwords, system settings
  Admin       – can manage viewer/annotator/reviewer roles, activate/deactivate users
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.middleware.rbac import require_admin, require_superadmin
from core.models.user import User
from core.models.background_task import BackgroundTask
from core.models.dataset import Dataset
from core.services.auth_service import hash_password, get_current_user
from core.services.audit_service import log_action

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

# Roles that admins (non-superadmin) are allowed to assign
ADMIN_ASSIGNABLE_ROLES = {"reviewer", "annotator", "viewer"}
ALL_VALID_ROLES = {"superadmin", "admin", "reviewer", "annotator", "viewer"}


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
    role: str  # superadmin, admin, reviewer, annotator, viewer


class UserStatusRequest(BaseModel):
    is_active: bool


class SystemStats(BaseModel):
    total_users: int
    active_users: int
    superadmin_count: int
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
    """Change a user's role.

    Permission rules:
    - SuperAdmin can assign ANY role (including admin).
    - Admin can only assign viewer / annotator / reviewer.
    - Nobody can modify a superadmin user (except another superadmin).
    - Nobody can promote to superadmin (system-only role).
    """
    is_superadmin = current_user.role == "superadmin"

    # Validate the requested role
    if body.role not in ALL_VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{body.role}'. Must be one of: {', '.join(sorted(ALL_VALID_ROLES))}",
        )

    # Superadmin is a system-only role — cannot be assigned via API
    if body.role == "superadmin":
        raise HTTPException(
            status_code=403,
            detail="The superadmin role is system-managed and cannot be assigned.",
        )

    # Only superadmin can promote/demote to/from admin
    if body.role == "admin" and not is_superadmin:
        raise HTTPException(
            status_code=403,
            detail="Only the SuperAdmin can assign the admin role.",
        )

    # Non-superadmin admins can only assign viewer/annotator/reviewer
    if not is_superadmin and body.role not in ADMIN_ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Admins can only assign: {', '.join(sorted(ADMIN_ASSIGNABLE_ROLES))}",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Cannot modify a superadmin user
    if user.role == "superadmin":
        raise HTTPException(
            status_code=403,
            detail="SuperAdmin account cannot be modified.",
        )

    # Non-superadmin admins cannot demote other admins
    if user.role == "admin" and not is_superadmin:
        raise HTTPException(
            status_code=403,
            detail="Only the SuperAdmin can change an admin's role.",
        )

    # Prevent self-demotion if last admin (superadmin excluded from count)
    if user.id == current_user.id and current_user.role == "admin" and body.role != "admin":
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
    """Activate or deactivate a user. Admin+ only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Cannot deactivate a superadmin
    if user.role == "superadmin":
        raise HTTPException(
            status_code=403, detail="SuperAdmin account cannot be deactivated."
        )

    # Cannot deactivate an admin unless you are superadmin
    if user.role == "admin" and current_user.role != "superadmin":
        raise HTTPException(
            status_code=403, detail="Only the SuperAdmin can deactivate admins."
        )

    # Prevent deactivating yourself
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
    current_user: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a user account. SuperAdmin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    # Cannot delete another superadmin
    if user.role == "superadmin":
        raise HTTPException(
            status_code=403, detail="SuperAdmin accounts cannot be deleted."
        )

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


# ── SuperAdmin-Only: Password Reset ─────────────────────────────────────


class PasswordResetRequest(BaseModel):
    new_password: str


@router.put("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: UUID,
    body: PasswordResetRequest,
    current_user: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Reset a user's password. SuperAdmin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.role == "superadmin" and user.id != current_user.id:
        raise HTTPException(
            status_code=403, detail="Cannot reset another superadmin's password."
        )

    await db.execute(
        sql_update(User)
        .where(User.id == user_id)
        .values(hashed_password=hash_password(body.new_password))
    )
    await db.commit()

    await log_action(
        db,
        user_id=current_user.id,
        action="admin.reset_password",
        resource_type="user",
        resource_id=str(user_id),
        details={"target_email": user.email},
    )

    return {"message": f"Password reset for {user.email}"}


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
    superadmin_count = (await db.execute(
        select(func.count()).where(User.role == "superadmin")
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
        superadmin_count=superadmin_count,
        admin_count=admin_count,
        total_datasets=total_datasets,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        failed_tasks=failed_tasks,
    )

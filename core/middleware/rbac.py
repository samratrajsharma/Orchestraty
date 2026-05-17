"""
Role-Based Access Control (RBAC) dependencies for FastAPI.

Usage in a route::

    @router.get("/admin-only", dependencies=[Depends(require_admin)])
    async def admin_endpoint(): ...
"""

from fastapi import Depends, HTTPException, status

from core.models.user import User
from core.services.auth_service import get_current_user


class RoleChecker:
    """Callable FastAPI dependency that enforces role membership.

    Parameters
    ----------
    allowed_roles:
        Roles that are permitted to access the protected resource.
    """

    def __init__(self, allowed_roles: list[str]) -> None:
        self.allowed_roles = allowed_roles

    async def __call__(
        self,
        current_user: User = Depends(get_current_user),
    ) -> User:
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user


# ── Pre-built dependency instances ────────────────────────────────────────

require_admin = RoleChecker(["admin"])
require_reviewer = RoleChecker(["admin", "reviewer"])
require_annotator = RoleChecker(["admin", "reviewer", "annotator"])
require_viewer = RoleChecker(["admin", "reviewer", "annotator", "viewer"])

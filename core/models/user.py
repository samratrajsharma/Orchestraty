"""
User model — authentication, roles, and organisation membership.
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(320), unique=True, nullable=False, index=True)
    hashed_password = Column(String(1024), nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(
        SAEnum(
            "superadmin", "admin", "reviewer", "annotator", "viewer",
            name="user_role",
        ),
        nullable=False,
        default="viewer",
        server_default="viewer",
    )
    org_id = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"

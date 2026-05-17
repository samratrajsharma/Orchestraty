"""
DatasetPermission model — row-level access control for datasets.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from core.database import Base


class DatasetPermission(Base):
    __tablename__ = "dataset_permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission_level = Column(
        SAEnum("owner", "editor", "viewer", name="permission_level"),
        nullable=False,
    )
    granted_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────
    dataset = relationship("Dataset", foreign_keys=[dataset_id])
    user = relationship("User", foreign_keys=[user_id])
    grantor = relationship("User", foreign_keys=[granted_by])

    def __repr__(self) -> str:
        return f"<DatasetPermission {self.user_id} -> {self.dataset_id} ({self.permission_level})>"

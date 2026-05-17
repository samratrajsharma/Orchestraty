"""
AuditLog model — immutable log of every significant action in the system.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action = Column(
        String(255),
        nullable=False,
        index=True,
        comment="create | read | update | delete | upload | download",
    )
    resource_type = Column(
        String(255),
        nullable=False,
        index=True,
        comment="dataset | version | user | permission",
    )
    resource_id = Column(String(255), nullable=False)
    details = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────
    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.resource_type}/{self.resource_id}>"

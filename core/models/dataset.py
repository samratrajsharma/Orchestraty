"""
Dataset, DatasetVersion, and DatasetMetadata models.
"""

import uuid

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from core.database import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(512), nullable=False)
    description = Column(Text, nullable=True)
    source_type = Column(
        String(50),
        nullable=False,
        comment="csv | json | api | database",
    )
    status = Column(
        SAEnum(
            "raw", "processed", "labeled", "reviewed", "ready",
            name="dataset_status",
        ),
        nullable=False,
        default="raw",
        server_default="raw",
    )
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    org_id = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
        server_default=func.now(),
    )

    # ── Relationships ─────────────────────────────────────────────────────
    versions = relationship(
        "DatasetVersion", back_populates="dataset", cascade="all, delete-orphan"
    )
    metadata_entries = relationship(
        "DatasetMetadata", back_populates="dataset", cascade="all, delete-orphan"
    )
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self) -> str:
        return f"<Dataset {self.name} ({self.status})>"


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number = Column(Integer, nullable=False)
    storage_path = Column(String(1024), nullable=False)
    file_name = Column(String(512), nullable=True)
    file_size = Column(BigInteger, nullable=True)
    file_type = Column(String(100), nullable=True)
    schema_hash = Column(String(128), nullable=True)
    row_count = Column(Integer, nullable=True)
    parent_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────────
    dataset = relationship("Dataset", back_populates="versions")
    parent_version = relationship("DatasetVersion", remote_side=[id])
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self) -> str:
        return f"<DatasetVersion {self.dataset_id} v{self.version_number}>"


class DatasetMetadata(Base):
    __tablename__ = "dataset_metadata"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key = Column(String(255), nullable=False)
    value = Column(Text, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
        server_default=func.now(),
    )

    # ── Relationships ─────────────────────────────────────────────────────
    dataset = relationship("Dataset", back_populates="metadata_entries")

    def __repr__(self) -> str:
        return f"<DatasetMetadata {self.key}={self.value!r}>"

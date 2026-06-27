"""
BackgroundTask model — tracks Celery task status in PostgreSQL.
Gives the API a queryable record of every async job.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, Float
from sqlalchemy.dialects.postgresql import UUID, JSONB

from core.database import Base


class BackgroundTask(Base):
    __tablename__ = "background_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    celery_task_id = Column(String(255), unique=True, nullable=False, index=True)

    # What kind of task: "structuring", "eda", "labeling"
    task_type = Column(String(50), nullable=False, index=True)

    # Current status: pending, started, progress, completed, failed
    status = Column(String(20), nullable=False, default="pending", index=True)

    # Progress 0.0 - 1.0
    progress = Column(Float, default=0.0)
    progress_message = Column(String(500), nullable=True)

    # Which dataset this task operates on (nullable so dataset-agnostic
    # entries — saved rule sets, etc. — can live in this table too)
    dataset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Who triggered it

    # Task input parameters and output results (flexible JSON)
    parameters = Column(JSONB, default=dict)
    result = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_bg_tasks_dataset_type", "dataset_id", "task_type"),
    )

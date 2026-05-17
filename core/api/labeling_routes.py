"""
Rule-Based Labeling Engine API — trigger labeling pipelines and inspect operators.
"""

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.middleware.rbac import require_annotator, require_viewer
from core.models.background_task import BackgroundTask
from core.models.dataset import Dataset, DatasetVersion
from core.models.user import User
from core.services.auth_service import get_current_user
from core.services.task_service import create_task_record
from core.services.audit_service import log_action

router = APIRouter(prefix="/api/v1/labeling", tags=["labeling"])


# ── Schemas ──────────────────────────────────────────────────────────────

# Derive operators from the engine's actual registry — single source of truth
import sys
from pathlib import Path as _Path

_DIS_DIR = _Path(__file__).resolve().parent.parent.parent / "data-intelligence-system"
if str(_DIS_DIR) not in sys.path:
    sys.path.insert(0, str(_DIS_DIR))

from labeling.rule_engine import SUPPORTED_OPERATORS  # noqa: E402

AVAILABLE_OPERATORS = sorted(SUPPORTED_OPERATORS)


class RuleDefinition(BaseModel):
    column: str
    operator: str
    value: Any
    label: str
    priority: int = 0


class LabelingRequest(BaseModel):
    dataset_id: UUID
    version_number: Optional[int] = None  # defaults to latest
    rules: Optional[list[RuleDefinition]] = None  # inline rule definitions
    rule_set_id: Optional[UUID] = None
    conflict_strategy: str = "first_match"


class LabelingResponse(BaseModel):
    task_id: UUID
    celery_task_id: str
    message: str


# ── Routes ───────────────────────────────────────────────────────────────


@router.post("/run", response_model=LabelingResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_labeling(
    payload: LabelingRequest,
    current_user: User = Depends(require_annotator),
    db: AsyncSession = Depends(get_db),
):
    """Trigger the Rule-Based Labeling pipeline on a dataset.

    Applies rule definitions to label data rows as a background task.
    Provide either inline `rules` or a `rule_set_id`. Returns immediately
    with a task ID to track progress.
    """
    # Validate: must provide either rules or rule_set_id
    if not payload.rules and not payload.rule_set_id:
        raise HTTPException(
            status_code=400,
            detail="Must provide either 'rules' (inline rule definitions) or 'rule_set_id'.",
        )

    # Verify dataset exists
    result = await db.execute(select(Dataset).where(Dataset.id == payload.dataset_id))
    dataset = result.scalars().first()
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    # Get the version to process
    if payload.version_number:
        version_stmt = select(DatasetVersion).where(
            DatasetVersion.dataset_id == payload.dataset_id,
            DatasetVersion.version_number == payload.version_number,
        )
    else:
        # Get latest version
        version_stmt = (
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == payload.dataset_id)
            .order_by(DatasetVersion.version_number.desc())
            .limit(1)
        )

    version_result = await db.execute(version_stmt)
    version = version_result.scalars().first()
    if version is None:
        msg = (
            f"Version {payload.version_number} not found for this dataset."
            if payload.version_number
            else "No file versions found for this dataset. Upload a file first."
        )
        raise HTTPException(status_code=400, detail=msg)

    # Serialize rules for Celery
    rules_payload = None
    if payload.rules:
        rules_payload = [r.model_dump() for r in payload.rules]

    # Pre-generate a Celery task ID so the DB record and Celery job share it
    import uuid as _uuid
    celery_task_id = str(_uuid.uuid4())

    # Create tracking record FIRST with the known ID
    task_record = await create_task_record(
        db=db,
        celery_task_id=celery_task_id,
        task_type="labeling",
        dataset_id=payload.dataset_id,
        created_by=current_user.id,
        parameters={
            "version_number": version.version_number,
            "rules": rules_payload,
            "rule_set_id": str(payload.rule_set_id) if payload.rule_set_id else None,
            "conflict_strategy": payload.conflict_strategy,
        },
    )

    await log_action(
        db,
        user_id=current_user.id,
        action="labeling.run",
        resource_type="dataset",
        resource_id=str(payload.dataset_id),
        details={
            "task_id": str(task_record.id),
            "version": version.version_number,
        },
    )

    # Commit so the row is visible to the Celery worker before dispatch
    await db.commit()

    # NOW submit the Celery task with the same pre-generated ID
    from data_intelligence.tasks.labeling_tasks import run_labeling_pipeline
    run_labeling_pipeline.apply_async(
        kwargs={
            "dataset_id": str(payload.dataset_id),
            "version_number": version.version_number,
            "rules": rules_payload,
            "rule_set_id": str(payload.rule_set_id) if payload.rule_set_id else None,
            "conflict_strategy": payload.conflict_strategy,
        },
        task_id=celery_task_id,
    )

    return LabelingResponse(
        task_id=task_record.id,
        celery_task_id=celery_task_id,
        message=f"Labeling pipeline started for dataset {dataset.name} v{version.version_number}",
    )


@router.get("/results/{dataset_id}")
async def get_labeling_results(
    dataset_id: UUID,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """Get the latest labeling results for a dataset."""
    stmt = (
        select(BackgroundTask)
        .where(
            BackgroundTask.dataset_id == dataset_id,
            BackgroundTask.task_type == "labeling",
            BackgroundTask.status == "completed",
        )
        .order_by(BackgroundTask.completed_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    task = result.scalars().first()

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="No completed labeling results found for this dataset",
        )

    return {
        "task_id": str(task.id),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "result": task.result,
    }


@router.get("/rules/operators")
async def list_operators(
    current_user: User = Depends(require_viewer),
):
    """List all available rule operators for the labeling engine."""
    return {
        "operators": AVAILABLE_OPERATORS,
    }

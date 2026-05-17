"""
Data Structuring Agent API — trigger and monitor data structuring pipelines.
"""

from typing import Literal, Optional
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

router = APIRouter(prefix="/api/v1/structuring", tags=["structuring"])


# ── Schemas ──────────────────────────────────────────────────────────────


class StructuringRequest(BaseModel):
    dataset_id: UUID
    version_number: Optional[int] = None  # defaults to latest
    null_strategy: Literal["fill_mode", "fill_mean", "fill_median", "fill_empty", "drop_rows"] = "fill_mode"
    remove_duplicates: bool = True
    handle_outliers: bool = False


class StructuringResponse(BaseModel):
    task_id: UUID
    celery_task_id: str
    message: str


# ── Routes ───────────────────────────────────────────────────────────────


@router.post("/run", response_model=StructuringResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_structuring(
    payload: StructuringRequest,
    current_user: User = Depends(require_annotator),
    db: AsyncSession = Depends(get_db),
):
    """Trigger the Data Structuring Agent on a dataset.

    Runs schema detection, data cleaning, and quality scoring as a
    background task. Returns immediately with a task ID to track progress.
    """
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

    # Pre-generate a Celery task ID so the DB record and Celery job share it
    import uuid as _uuid
    celery_task_id = str(_uuid.uuid4())

    # Create tracking record FIRST with the known ID
    task_record = await create_task_record(
        db=db,
        celery_task_id=celery_task_id,
        task_type="structuring",
        dataset_id=payload.dataset_id,
        created_by=current_user.id,
        parameters={
            "version_number": version.version_number,
            "null_strategy": payload.null_strategy,
            "remove_duplicates": payload.remove_duplicates,
            "handle_outliers": payload.handle_outliers,
        },
    )

    await log_action(
        db,
        user_id=current_user.id,
        action="structuring.run",
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
    from data_intelligence.tasks.structuring_tasks import run_structuring_pipeline
    run_structuring_pipeline.apply_async(
        kwargs={
            "dataset_id": str(payload.dataset_id),
            "version_number": version.version_number,
            "null_strategy": payload.null_strategy,
            "remove_dupes": payload.remove_duplicates,
            "handle_outliers": payload.handle_outliers,
        },
        task_id=celery_task_id,
    )

    return StructuringResponse(
        task_id=task_record.id,
        celery_task_id=celery_task_id,
        message=f"Structuring pipeline started for dataset {dataset.name} v{version.version_number}",
    )


@router.get("/results/{dataset_id}")
async def get_structuring_results(
    dataset_id: UUID,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """Get the latest structuring results for a dataset."""
    stmt = (
        select(BackgroundTask)
        .where(
            BackgroundTask.dataset_id == dataset_id,
            BackgroundTask.task_type == "structuring",
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
            detail="No completed structuring results found for this dataset",
        )

    return {
        "task_id": str(task.id),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "result": task.result,
    }

"""
AI-Powered EDA API — trigger and monitor exploratory data analysis pipelines.
"""

from typing import Optional
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

router = APIRouter(prefix="/api/v1/eda", tags=["eda"])


# ── Schemas ──────────────────────────────────────────────────────────────


class EDARequest(BaseModel):
    dataset_id: UUID
    version_number: Optional[int] = None  # defaults to latest
    run_profiling: bool = True
    run_embeddings: bool = True
    run_clustering: bool = True
    n_clusters: int = 5


class EDAResponse(BaseModel):
    task_id: UUID
    celery_task_id: str
    message: str


# ── Routes ───────────────────────────────────────────────────────────────


@router.post("/run", response_model=EDAResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_eda(
    payload: EDARequest,
    current_user: User = Depends(require_annotator),
    db: AsyncSession = Depends(get_db),
):
    """Trigger the AI-Powered EDA pipeline on a dataset.

    Runs data profiling, embedding generation, and clustering as a
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
        task_type="eda",
        dataset_id=payload.dataset_id,
        created_by=current_user.id,
        parameters={
            "version_number": version.version_number,
            "run_profiling": payload.run_profiling,
            "run_embeddings": payload.run_embeddings,
            "run_clustering": payload.run_clustering,
            "n_clusters": payload.n_clusters,
        },
    )

    await log_action(
        db,
        user_id=current_user.id,
        action="eda.run",
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
    from data_intelligence.tasks.eda_tasks import run_eda_pipeline
    run_eda_pipeline.apply_async(
        kwargs={
            "dataset_id": str(payload.dataset_id),
            "version_number": version.version_number,
            "run_profiling": payload.run_profiling,
            "run_embeddings": payload.run_embeddings,
            "run_clustering": payload.run_clustering,
            "n_clusters": payload.n_clusters,
        },
        task_id=celery_task_id,
    )

    return EDAResponse(
        task_id=task_record.id,
        celery_task_id=celery_task_id,
        message=f"EDA pipeline started for dataset {dataset.name} v{version.version_number}",
    )


@router.get("/results/{dataset_id}")
async def get_eda_results(
    dataset_id: UUID,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """Get the latest EDA results for a dataset."""
    stmt = (
        select(BackgroundTask)
        .where(
            BackgroundTask.dataset_id == dataset_id,
            BackgroundTask.task_type == "eda",
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
            detail="No completed EDA results found for this dataset",
        )

    return {
        "task_id": str(task.id),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "result": task.result,
    }


@router.get("/profile/{dataset_id}")
async def get_eda_profile(
    dataset_id: UUID,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """Get just the profiling section from the latest EDA results."""
    stmt = (
        select(BackgroundTask)
        .where(
            BackgroundTask.dataset_id == dataset_id,
            BackgroundTask.task_type == "eda",
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
            detail="No completed EDA results found for this dataset",
        )

    profiling = task.result.get("profiling") if isinstance(task.result, dict) else None
    if profiling is None:
        raise HTTPException(
            status_code=404,
            detail="No profiling data found in the latest EDA results",
        )

    return {
        "task_id": str(task.id),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "profiling": profiling,
    }

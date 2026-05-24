"""
FastAPI router for the Data Ingestion API.

Prefix: /api/v1/datasets

All endpoints require authentication.  Role gates are enforced via the
RBAC middleware in ``core.middleware.rbac``.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from typing import Optional
from uuid import UUID

import csv as csv_mod

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database import get_db
from core.middleware.rbac import (
    require_admin,
    require_annotator,
    require_reviewer,
    require_viewer,
)
from core.models.dataset import Dataset, DatasetMetadata, DatasetVersion
from core.models.permission import DatasetPermission
from core.models.user import User
from core.services.audit_service import log_action
from core.services.auth_service import get_current_user

from .schemas import (
    DatasetCreate,
    DatasetListResponse,
    DatasetMetadataResponse,
    DatasetMetadataUpdate,
    DatasetResponse,
    DatasetStatusUpdate,
    DatasetVersionResponse,
    PermissionCreate,
    PermissionResponse,
    UploadResponse,
    ValidationResultResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB
ALLOWED_FILE_TYPES = {"text/csv", "application/json", "text/plain"}
ALLOWED_EXTENSIONS = {".csv", ".json", ".jsonl", ".txt", ".tsv"}


def _get_extension(filename: str) -> str:
    """Return the lowercased file extension including the leading dot."""
    idx = filename.rfind(".")
    return filename[idx:].lower() if idx != -1 else ""


async def _get_dataset_or_404(
    db: AsyncSession, dataset_id: UUID
) -> Dataset:
    """Fetch a dataset by primary key or raise 404."""
    result = await db.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalars().first()
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset {dataset_id} not found",
        )
    return dataset


def _validate_file_content(
    raw_bytes: bytes,
    file_name: str,
    file_type: str,
) -> ValidationResultResponse:
    """Perform lightweight validation on an uploaded file.

    Returns a ``ValidationResultResponse`` with schema information and any
    errors or warnings detected.
    """
    errors: list[str] = []
    warnings: list[str] = []
    columns: list[str] = []
    row_count = 0
    column_count = 0
    sample_data: list[dict] = []

    ext = _get_extension(file_name)
    schema_hash = hashlib.sha256(raw_bytes[:8192]).hexdigest()

    try:
        if ext in (".csv", ".tsv"):
            delimiter = "\t" if ext == ".tsv" else ","
            text = raw_bytes.decode("utf-8")
            reader = csv_mod.DictReader(io.StringIO(text), delimiter=delimiter)
            columns = reader.fieldnames or []
            column_count = len(columns)
            rows: list[dict] = []
            for row in reader:
                rows.append(row)
            row_count = len(rows)
            sample_data = rows[:5]
            if row_count == 0:
                warnings.append("File contains headers but no data rows")
        elif ext in (".json", ".jsonl"):
            text = raw_bytes.decode("utf-8")
            if ext == ".jsonl":
                lines = [ln for ln in text.strip().splitlines() if ln.strip()]
                parsed = [json.loads(ln) for ln in lines]
            else:
                parsed_raw = json.loads(text)
                parsed = parsed_raw if isinstance(parsed_raw, list) else [parsed_raw]
            row_count = len(parsed)
            if parsed and isinstance(parsed[0], dict):
                columns = list(parsed[0].keys())
                column_count = len(columns)
            sample_data = parsed[:5]
            if row_count == 0:
                warnings.append("JSON file contains no records")
        elif ext == ".txt":
            text = raw_bytes.decode("utf-8")
            lines = text.strip().splitlines()
            row_count = len(lines)
            columns = ["line"]
            column_count = 1
            sample_data = [{"line": ln} for ln in lines[:5]]
        else:
            errors.append(f"Unsupported file extension: {ext}")
    except UnicodeDecodeError:
        errors.append("File encoding is not valid UTF-8")
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid JSON: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Validation error: {exc}")

    return ValidationResultResponse(
        is_valid=len(errors) == 0,
        row_count=row_count,
        column_count=column_count,
        columns=columns,
        schema_hash=schema_hash,
        errors=errors,
        warnings=warnings,
        sample_data=sample_data,
    )


# ---------------------------------------------------------------------------
# POST / — Create dataset
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=DatasetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset(
    payload: DatasetCreate,
    current_user: User = Depends(require_annotator),
    db: AsyncSession = Depends(get_db),
):
    """Create a new dataset record (no file upload yet)."""
    dataset = Dataset(
        name=payload.name,
        description=payload.description,
        source_type=payload.source_type,
        status="raw",
        created_by=current_user.id,
        org_id=current_user.org_id,
    )
    db.add(dataset)
    await db.flush()
    await db.refresh(dataset)

    await log_action(
        db,
        user_id=current_user.id,
        action="dataset.create",
        resource_type="dataset",
        resource_id=str(dataset.id),
        details={"name": dataset.name, "source_type": dataset.source_type},
    )

    return dataset


# ---------------------------------------------------------------------------
# POST /{dataset_id}/upload — Upload file to dataset
# ---------------------------------------------------------------------------


@router.post(
    "/{dataset_id}/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    dataset_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_annotator),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file to an existing dataset.

    The file is validated, stored in MinIO (object storage), and a new
    ``DatasetVersion`` is created.
    """
    dataset = await _get_dataset_or_404(db, dataset_id)

    # --- basic file checks ---
    file_name = file.filename or "upload"
    ext = _get_extension(file_name)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{ext}' is not supported. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    raw_bytes = await file.read()
    file_size = len(raw_bytes)
    if file_size > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File exceeds maximum upload size of {MAX_UPLOAD_SIZE // (1024 * 1024)} MB",
        )
    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    # --- validate content ---
    file_type = file.content_type or "application/octet-stream"
    validation = _validate_file_content(raw_bytes, file_name, file_type)
    if not validation.is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "File validation failed",
                "errors": validation.errors,
            },
        )

    # --- determine version number ---
    latest_stmt = (
        select(func.max(DatasetVersion.version_number))
        .where(DatasetVersion.dataset_id == dataset_id)
    )
    result = await db.execute(latest_stmt)
    latest_version = result.scalar() or 0
    new_version_number = latest_version + 1

    # --- store in MinIO (object storage) ---
    storage_path = (
        f"datasets/{dataset_id}/v{new_version_number}/{file_name}"
    )
    try:
        # Lazy import — the storage module may not be installed in every
        # test environment but we must not let that break route loading.
        from ..storage.minio_client import minio_storage  # type: ignore[import-untyped]

        await minio_storage.upload_file(
            file_data=raw_bytes,
            file_name=file_name,
            content_type=file_type,
            dataset_id=str(dataset_id),
            version=new_version_number,
        )
    except ImportError:
        logger.warning(
            "MinIO storage module not available; file stored at logical path only"
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to upload to MinIO: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store file in object storage",
        ) from exc

    # --- find parent version id (if any) ---
    parent_version_id = None
    if latest_version > 0:
        parent_stmt = (
            select(DatasetVersion.id)
            .where(
                DatasetVersion.dataset_id == dataset_id,
                DatasetVersion.version_number == latest_version,
            )
        )
        parent_result = await db.execute(parent_stmt)
        parent_version_id = parent_result.scalar()

    # --- create version record ---
    version = DatasetVersion(
        dataset_id=dataset_id,
        version_number=new_version_number,
        storage_path=storage_path,
        file_name=file_name,
        file_size=file_size,
        file_type=file_type,
        schema_hash=validation.schema_hash,
        row_count=validation.row_count,
        parent_version_id=parent_version_id,
        created_by=current_user.id,
    )
    db.add(version)
    await db.flush()
    await db.refresh(version)
    await db.refresh(dataset)

    await log_action(
        db,
        user_id=current_user.id,
        action="dataset.upload",
        resource_type="dataset_version",
        resource_id=str(version.id),
        details={
            "dataset_id": str(dataset_id),
            "version": new_version_number,
            "file_name": file_name,
            "file_size": file_size,
        },
    )

    return UploadResponse(
        dataset=DatasetResponse.model_validate(dataset),
        version=DatasetVersionResponse.model_validate(version),
        validation=validation,
    )


# ---------------------------------------------------------------------------
# GET / — List datasets
# ---------------------------------------------------------------------------


@router.get("", response_model=DatasetListResponse)
async def list_datasets(
    status_filter: Optional[str] = Query(
        None, alias="status", pattern="^(raw|processed|labeled|reviewed|ready)$"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """Return a paginated list of datasets, optionally filtered by status."""
    base = select(Dataset)
    count_base = select(func.count(Dataset.id))

    if status_filter:
        base = base.where(Dataset.status == status_filter)
        count_base = count_base.where(Dataset.status == status_filter)

    # total count
    total_result = await db.execute(count_base)
    total = total_result.scalar() or 0

    # paginated rows
    stmt = base.order_by(Dataset.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    datasets = list(result.scalars().all())

    return DatasetListResponse(
        datasets=[DatasetResponse.model_validate(d) for d in datasets],
        total=total,
        skip=skip,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# GET /{dataset_id} — Dataset detail (with versions + metadata)
# ---------------------------------------------------------------------------


@router.get("/{dataset_id}")
async def get_dataset(
    dataset_id: UUID,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """Return full dataset detail including versions and metadata."""
    stmt = (
        select(Dataset)
        .options(
            selectinload(Dataset.versions),
            selectinload(Dataset.metadata_entries),
        )
        .where(Dataset.id == dataset_id)
    )
    result = await db.execute(stmt)
    dataset = result.scalars().first()
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset {dataset_id} not found",
        )

    return {
        "dataset": DatasetResponse.model_validate(dataset),
        "versions": [
            DatasetVersionResponse.model_validate(v) for v in dataset.versions
        ],
        "metadata": [
            DatasetMetadataResponse.model_validate(m)
            for m in dataset.metadata_entries
        ],
    }


# ---------------------------------------------------------------------------
# GET /{dataset_id}/versions — List versions
# ---------------------------------------------------------------------------


@router.get(
    "/{dataset_id}/versions",
    response_model=list[DatasetVersionResponse],
)
async def list_versions(
    dataset_id: UUID,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """Return all versions of a dataset ordered by version number."""
    await _get_dataset_or_404(db, dataset_id)

    stmt = (
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_number.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# PUT /{dataset_id}/status — Update lifecycle status
# ---------------------------------------------------------------------------


@router.put("/{dataset_id}/status", response_model=DatasetResponse)
async def update_status(
    dataset_id: UUID,
    payload: DatasetStatusUpdate,
    current_user: User = Depends(require_reviewer),
    db: AsyncSession = Depends(get_db),
):
    """Transition a dataset to a new lifecycle status.

    Only reviewers and admins may perform status transitions.
    """
    dataset = await _get_dataset_or_404(db, dataset_id)
    old_status = dataset.status
    dataset.status = payload.status
    await db.flush()
    await db.refresh(dataset)

    await log_action(
        db,
        user_id=current_user.id,
        action="dataset.status_update",
        resource_type="dataset",
        resource_id=str(dataset.id),
        details={"old_status": old_status, "new_status": payload.status},
    )

    return dataset


# ---------------------------------------------------------------------------
# PUT /{dataset_id}/metadata — Add / update metadata
# ---------------------------------------------------------------------------


@router.put(
    "/{dataset_id}/metadata",
    response_model=DatasetMetadataResponse,
)
async def upsert_metadata(
    dataset_id: UUID,
    payload: DatasetMetadataUpdate,
    current_user: User = Depends(require_annotator),
    db: AsyncSession = Depends(get_db),
):
    """Create or update a metadata key-value pair on a dataset."""
    await _get_dataset_or_404(db, dataset_id)

    # Check if key already exists for this dataset
    stmt = select(DatasetMetadata).where(
        DatasetMetadata.dataset_id == dataset_id,
        DatasetMetadata.key == payload.key,
    )
    result = await db.execute(stmt)
    existing = result.scalars().first()

    if existing:
        existing.value = payload.value
        await db.flush()
        await db.refresh(existing)
        meta = existing
    else:
        meta = DatasetMetadata(
            dataset_id=dataset_id,
            key=payload.key,
            value=payload.value,
        )
        db.add(meta)
        await db.flush()
        await db.refresh(meta)

    await log_action(
        db,
        user_id=current_user.id,
        action="dataset.metadata_upsert",
        resource_type="dataset_metadata",
        resource_id=str(meta.id),
        details={
            "dataset_id": str(dataset_id),
            "key": payload.key,
        },
    )

    return meta


# ---------------------------------------------------------------------------
# POST /{dataset_id}/permissions — Grant permission
# ---------------------------------------------------------------------------


@router.post(
    "/{dataset_id}/permissions",
    response_model=PermissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_permission(
    dataset_id: UUID,
    payload: PermissionCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Grant a user a permission level on a dataset.  Admin only."""
    await _get_dataset_or_404(db, dataset_id)

    # Verify target user exists
    target_result = await db.execute(
        select(User).where(User.id == payload.user_id)
    )
    if target_result.scalars().first() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {payload.user_id} not found",
        )

    # Upsert — replace existing permission for the same user + dataset
    existing_stmt = select(DatasetPermission).where(
        DatasetPermission.dataset_id == dataset_id,
        DatasetPermission.user_id == payload.user_id,
    )
    existing_result = await db.execute(existing_stmt)
    existing = existing_result.scalars().first()

    if existing:
        existing.permission_level = payload.permission_level
        existing.granted_by = current_user.id
        await db.flush()
        await db.refresh(existing)
        perm = existing
    else:
        perm = DatasetPermission(
            dataset_id=dataset_id,
            user_id=payload.user_id,
            permission_level=payload.permission_level,
            granted_by=current_user.id,
        )
        db.add(perm)
        await db.flush()
        await db.refresh(perm)

    await log_action(
        db,
        user_id=current_user.id,
        action="dataset.permission_grant",
        resource_type="dataset_permission",
        resource_id=str(perm.id),
        details={
            "dataset_id": str(dataset_id),
            "target_user": str(payload.user_id),
            "level": payload.permission_level,
        },
    )

    return perm


# ---------------------------------------------------------------------------
# DELETE /{dataset_id} — Delete dataset
# ---------------------------------------------------------------------------


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a dataset and all related records.  Admin only.

    Versions, metadata, and permissions are cascade-deleted by the database.
    Stored files in MinIO are cleaned up on a best-effort basis.
    """
    dataset = await _get_dataset_or_404(db, dataset_id)

    # Attempt to remove objects from MinIO
    try:
        from ..storage.minio_client import minio_storage  # type: ignore[import-untyped]

        versions_stmt = select(DatasetVersion).where(
            DatasetVersion.dataset_id == dataset_id
        )
        versions_result = await db.execute(versions_stmt)
        for version in versions_result.scalars().all():
            try:
                await minio_storage.delete_file(version.storage_path)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to delete MinIO object: %s", version.storage_path
                )
    except ImportError:
        logger.warning("MinIO storage module not available; skipping object cleanup")

    dataset_name = dataset.name
    await db.delete(dataset)
    await db.flush()

    await log_action(
        db,
        user_id=current_user.id,
        action="dataset.delete",
        resource_type="dataset",
        resource_id=str(dataset_id),
        details={"name": dataset_name},
    )

    return None

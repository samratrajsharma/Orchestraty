"""
Celery tasks for the Data Structuring Agent.
Runs schema detection, cleaning, and quality scoring as a background job.
"""

import io
import json
import logging
import sys
from pathlib import Path

# Ensure project root is on path for imports
_PROJECT_ROOT = str(Path(__file__).parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd
from celery import current_task

from core.celery_app import app
from core.database_sync import SyncSessionLocal
from core.services.task_service import update_task_progress, complete_task

logger = logging.getLogger(__name__)


def _load_dataset_file(dataset_id: str, version_number: int) -> pd.DataFrame:
    """Download the dataset file from MinIO and load as DataFrame."""
    from core.settings import settings
    from minio import Minio

    client = Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_USE_SSL,
    )

    # Find the file in MinIO by listing objects with prefix
    prefix = f"datasets/{dataset_id}/v{version_number}/"
    objects = list(client.list_objects(settings.MINIO_BUCKET_NAME, prefix=prefix))

    if not objects:
        raise FileNotFoundError(f"No files found at {prefix}")

    obj = objects[0]
    response = client.get_object(settings.MINIO_BUCKET_NAME, obj.object_name)
    raw_bytes = response.read()
    response.close()
    response.release_conn()

    # Detect file type and load
    file_name = obj.object_name.split("/")[-1].lower()
    if file_name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(raw_bytes))
    elif file_name.endswith(".tsv"):
        return pd.read_csv(io.BytesIO(raw_bytes), sep="\t")
    elif file_name.endswith(".json"):
        return pd.read_json(io.BytesIO(raw_bytes))
    elif file_name.endswith(".jsonl"):
        return pd.read_json(io.BytesIO(raw_bytes), lines=True)
    else:
        # Try CSV as fallback
        return pd.read_csv(io.BytesIO(raw_bytes))


def _save_cleaned_file(
    df: pd.DataFrame,
    dataset_id: str,
    version_number: int,
    original_filename: str,
) -> str:
    """Upload cleaned DataFrame back to MinIO as a new file."""
    from core.settings import settings
    from minio import Minio

    client = Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_USE_SSL,
    )

    # Save as CSV
    buffer = io.BytesIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)
    data_length = buffer.getbuffer().nbytes

    clean_name = f"cleaned_{original_filename}"
    if not clean_name.endswith(".csv"):
        clean_name = clean_name.rsplit(".", 1)[0] + ".csv"

    object_name = f"datasets/{dataset_id}/v{version_number}/{clean_name}"

    client.put_object(
        bucket_name=settings.MINIO_BUCKET_NAME,
        object_name=object_name,
        data=buffer,
        length=data_length,
        content_type="text/csv",
    )

    logger.info("Saved cleaned file: %s (%d bytes)", object_name, data_length)
    return object_name


@app.task(bind=True, name="structuring.run_pipeline")
def run_structuring_pipeline(
    self,
    dataset_id: str,
    version_number: int,
    null_strategy: str = "fill_mode",
    remove_dupes: bool = True,
    handle_outliers: bool = False,
):
    """Run the full data structuring pipeline: detect → clean → score.

    Args:
        dataset_id: UUID of the dataset.
        version_number: Which version's file to process.
        null_strategy: How to handle nulls.
        remove_dupes: Whether to deduplicate.
        handle_outliers: Whether to remove outliers.
    """
    celery_task_id = self.request.id
    db = SyncSessionLocal()

    try:
        # ── Step 1: Load data ────────────────────────────────────────────
        update_task_progress(db, celery_task_id, 0.1, "Loading dataset from storage...")

        df = _load_dataset_file(dataset_id, version_number)
        logger.info("Loaded dataset %s v%d: %d rows, %d columns",
                     dataset_id, version_number, len(df), len(df.columns))

        # ── Step 2: Schema detection ─────────────────────────────────────
        update_task_progress(db, celery_task_id, 0.3, "Detecting column types...")

        # Import from the hyphenated directory
        sys.path.insert(0, str(Path(_PROJECT_ROOT) / "data-intelligence-system"))
        from structuring.schema_detector import detect_schema
        schema = detect_schema(df)

        # ── Step 3: Clean data ───────────────────────────────────────────
        update_task_progress(db, celery_task_id, 0.5, "Cleaning data...")

        from structuring.cleaning_pipeline import run_cleaning_pipeline
        cleaned_df, cleaning_report = run_cleaning_pipeline(
            df,
            null_strategy=null_strategy,
            remove_dupes=remove_dupes,
            handle_outliers=handle_outliers,
        )

        # ── Step 4: Quality scoring ──────────────────────────────────────
        update_task_progress(db, celery_task_id, 0.7, "Scoring data quality...")

        from structuring.quality_scorer import score_quality
        quality_report = score_quality(cleaned_df)

        # ── Step 5: Save cleaned file ────────────────────────────────────
        update_task_progress(db, celery_task_id, 0.85, "Saving cleaned dataset...")

        # Get original filename
        from core.settings import settings
        from minio import Minio
        client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_USE_SSL,
        )
        prefix = f"datasets/{dataset_id}/v{version_number}/"
        objects = list(client.list_objects(settings.MINIO_BUCKET_NAME, prefix=prefix))
        original_filename = objects[0].object_name.split("/")[-1] if objects else "data.csv"

        cleaned_path = _save_cleaned_file(cleaned_df, dataset_id, version_number, original_filename)

        # ── Step 6: Update dataset status ────────────────────────────────
        update_task_progress(db, celery_task_id, 0.95, "Updating dataset status...")

        from sqlalchemy import update as sql_update
        from core.models.dataset import Dataset
        db.execute(
            sql_update(Dataset)
            .where(Dataset.id == dataset_id)
            .values(status="processed")
        )
        db.commit()

        # ── Done ─────────────────────────────────────────────────────────
        result = {
            "schema": schema.to_dict(),
            "cleaning": cleaning_report.to_dict(),
            "quality": quality_report.to_dict(),
            "cleaned_file_path": cleaned_path,
            "original_rows": schema.row_count,
            "final_rows": cleaning_report.final_rows,
        }

        complete_task(db, celery_task_id, result=result)
        logger.info(
            "Structuring complete for dataset %s: quality=%.1f (%s)",
            dataset_id, quality_report.overall_score, quality_report.grade,
        )
        return result

    except Exception as exc:
        logger.error("Structuring failed for dataset %s: %s", dataset_id, exc, exc_info=True)
        complete_task(db, celery_task_id, error=str(exc))
        raise
    finally:
        db.close()

"""
Celery tasks for the Rule-Based Labeling Engine.
Applies labeling rules to datasets as a background job.
"""

import logging
import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.celery_app import app

logger = logging.getLogger(__name__)


@app.task(bind=True, name="labeling.run_pipeline")
def run_labeling_pipeline(
    self,
    dataset_id: str,
    version_number: int,
    rules: list | None = None,
    rule_set_id: str | None = None,
    conflict_strategy: str = "first_match",
):
    """Apply labeling rules to a dataset.

    Args:
        dataset_id: UUID of the dataset.
        version_number: Which version's file to process.
        rules: Inline rule definitions (list of dicts).
        rule_set_id: ID of a saved rule set to apply.
        conflict_strategy: How to handle conflicts: first_match, majority, priority.
    """
    celery_task_id = self.request.id
    from core.database_sync import SyncSessionLocal
    from core.services.task_service import update_task_progress, complete_task

    db = SyncSessionLocal()

    try:
        # ── Step 1: Load data ────────────────────────────────────────────
        update_task_progress(db, celery_task_id, 0.1, "Loading dataset from storage...")

        from data_intelligence.tasks.structuring_tasks import _load_dataset_file
        df = _load_dataset_file(dataset_id, version_number)
        logger.info("Labeling: Loaded dataset %s v%d: %d rows", dataset_id, version_number, len(df))

        # ── Step 2: Load or parse rules ──────────────────────────────────
        update_task_progress(db, celery_task_id, 0.25, "Loading labeling rules...")
        sys.path.insert(0, str(Path(_PROJECT_ROOT) / "data-intelligence-system"))
        from labeling.rule_engine import RuleEngine

        engine = RuleEngine(conflict_strategy=conflict_strategy)

        if rule_set_id:
            from labeling.rule_store import load_rule_set
            rule_defs = load_rule_set(db, rule_set_id)
            engine.load_rules(rule_defs)
        elif rules:
            engine.load_rules(rules)
        else:
            raise ValueError("Either 'rules' or 'rule_set_id' must be provided")

        # ── Step 3: Apply rules ──────────────────────────────────────────
        update_task_progress(db, celery_task_id, 0.5, "Applying labeling rules...")
        labeled_df, labeling_report = engine.apply(df)

        # ── Step 4: Save labeled file ────────────────────────────────────
        update_task_progress(db, celery_task_id, 0.75, "Saving labeled dataset...")

        from data_intelligence.tasks.structuring_tasks import _save_cleaned_file
        labeled_path = _save_cleaned_file(labeled_df, dataset_id, version_number, "labeled_data.csv")

        # ── Done ─────────────────────────────────────────────────────────
        result = {
            "labeling": labeling_report.to_dict(),
            "labeled_file_path": labeled_path,
            "total_rows": len(labeled_df),
            "labeled_rows": labeling_report.labeled_count,
            "unlabeled_rows": labeling_report.unlabeled_count,
        }

        update_task_progress(db, celery_task_id, 0.95, "Finalizing labeling results...")
        complete_task(db, celery_task_id, result=result)
        logger.info("Labeling complete for dataset %s: %d/%d labeled",
                     dataset_id, labeling_report.labeled_count, len(labeled_df))
        return result

    except Exception as exc:
        logger.error("Labeling failed for dataset %s: %s", dataset_id, exc, exc_info=True)
        complete_task(db, celery_task_id, error=str(exc))
        raise
    finally:
        db.close()

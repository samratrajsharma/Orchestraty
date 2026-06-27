"""
Central model registry — import all models so Alembic and init_db() can discover them.

Phase D — users / orgs / audit_logs / dataset_permissions tables are gone.
"""

from core.database import Base

from core.models.dataset import Dataset, DatasetVersion, DatasetMetadata
from core.models.background_task import BackgroundTask
from core.models.llm_config import LLMConfigRecord
from core.models.image_asset import ImageAsset
from core.models.workflow import Workflow, WorkflowTemplate

__all__ = [
    "Base",
    "Dataset",
    "DatasetVersion",
    "DatasetMetadata",
    "BackgroundTask",
    "LLMConfigRecord",
    "ImageAsset",
    "Workflow",
    "WorkflowTemplate",
]

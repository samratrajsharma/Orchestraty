"""
Central model registry — import all models so Alembic and init_db() can discover them.
"""

from core.database import Base

from core.models.user import User
from core.models.dataset import Dataset, DatasetVersion, DatasetMetadata
from core.models.audit import AuditLog
from core.models.permission import DatasetPermission
from core.models.background_task import BackgroundTask
from core.models.llm_config import LLMConfigRecord
from core.models.image_asset import ImageAsset
from core.models.workflow import Workflow, WorkflowTemplate

__all__ = [
    "Base",
    "User",
    "Dataset",
    "DatasetVersion",
    "DatasetMetadata",
    "AuditLog",
    "DatasetPermission",
    "BackgroundTask",
    "LLMConfigRecord",
    "ImageAsset",
    "Workflow",
    "WorkflowTemplate",
]

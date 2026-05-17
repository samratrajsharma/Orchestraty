"""
Celery application configuration for Orchestraty.
Handles async background tasks: data structuring, EDA, labeling.
"""

from celery import Celery

from core.settings import settings

app = Celery(
    "orchestraty",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Task behavior
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # Result expiry (24 hours)
    result_expires=86400,

    # Auto-discover tasks in these modules
    include=[
        "data_intelligence.tasks.structuring_tasks",
        "data_intelligence.tasks.eda_tasks",
        "data_intelligence.tasks.labeling_tasks",
        "data_intelligence.tasks.image_tasks",
        "data_intelligence.tasks.ai_labeling_tasks",
        "data_intelligence.tasks.review_tasks",
        "data_intelligence.tasks.workflow_tasks",
    ],
)

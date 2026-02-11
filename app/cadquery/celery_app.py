import os
from celery import Celery
from celery.schedules import crontab

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")

celery_app = Celery(
    "cadquery_worker",
    broker=redis_url,
    backend=redis_url,
    include=["app.cadquery.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    beat_schedule={
        "prune-generated-files-every-5-hours": {
            "task": "app.cadquery.tasks.prune_generated_files",
            "schedule": 18000.0, # 5 hours in seconds
        },
    }
)

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "timekeeper",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.timezone = "UTC"
celery_app.conf.enable_utc = True
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.beat_schedule = {
    "dispatch-reminders-every-minute": {
        "task": "app.scheduler.tasks.dispatch_due_notifications",
        "schedule": settings.scheduler_poll_seconds,
    },
    "daily-lessons-digest-every-5-min": {
        "task": "app.scheduler.tasks.send_daily_lessons_digest",
        "schedule": 300,
    },
    "deliver-outbox-every-30-sec": {
        "task": "app.scheduler.tasks.deliver_outbox",
        "schedule": 30,
    },
}
celery_app.conf.imports = ("app.scheduler.tasks",)

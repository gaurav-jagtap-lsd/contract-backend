import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "contractvault_api.settings")

app = Celery("contractvault")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    # Runs every day at 08:00 UTC — main reminder dispatch
    "send-contract-reminders-daily": {
        "task": "apps.reminders.tasks.dispatch_reminders",
        "schedule": crontab(hour=8, minute=0),
    },
    # Runs every hour — update contract statuses (active → expiring soon → expired)
    "update-contract-statuses": {
        "task": "apps.contracts.tasks.update_contract_statuses",
        "schedule": crontab(minute=0),
    },
}

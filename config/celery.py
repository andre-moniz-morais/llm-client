"""The Celery app that runs chat turns and polls generations.

Started in its own container (see docker-compose.prod.yml). Without a broker
configured, ``CELERY_TASK_ALWAYS_EAGER`` makes every task run inline, so local
development and the tests need no Redis.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("craft")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

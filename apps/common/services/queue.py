"""Handing work to the Celery worker.

Without a broker (local development, the tests) ``CELERY_TASK_ALWAYS_EAGER`` is
on and the task simply runs here, now - so callers never branch on whether a
worker exists.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction


def enqueue(task, *args, **options) -> None:
    """Run ``task`` in the background once the current transaction commits.

    Waiting for the commit matters: a worker fast enough to start before it
    would look for a row that does not exist yet.
    """
    if settings.CELERY_TASK_ALWAYS_EAGER:
        task.apply(args=args, throw=True)
        return
    transaction.on_commit(lambda: task.apply_async(args=args, **options))

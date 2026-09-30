"""Generation work done on the Celery worker.

Celery beat runs :func:`poll_running` on a short interval; it fans out one
:func:`poll` per unfinished generation. That moves tasks forward, stores their
files and sends the Telegram notification whether or not a browser is open.
"""

from __future__ import annotations

from celery import shared_task
from django.db import transaction

from apps.generation.models import Generation

UNFINISHED = [Generation.Status.PENDING, Generation.Status.RUNNING]


@shared_task
def poll_running() -> int:
    ids = list(Generation.objects.filter(status__in=UNFINISHED).values_list("pk", flat=True))
    for pk in ids:
        # Expiring keeps a backlog from piling up behind a slow download: the
        # next sweep queues a fresh poll anyway.
        poll.apply_async(args=[pk], expires=60)
    return len(ids)


@shared_task
def poll(generation_id: int) -> None:
    """Check one generation, unless another worker is already on it."""
    from apps.generation.services import engine

    with transaction.atomic():
        # The row lock is held through the download, so a second sweep cannot
        # store the same assets twice; it skips the row instead of waiting.
        generation = (
            Generation.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("user__settings")
            .filter(pk=generation_id, status__in=UNFINISHED)
            .first()
        )
        if generation is not None:
            engine.refresh(generation)

"""Chat work done on the Celery worker."""

from __future__ import annotations

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from apps.chat.models import Message

logger = logging.getLogger(__name__)


@shared_task
def complete_turn(message_id: int) -> None:
    """Produce the assistant reply that :func:`engine.send_message` left pending."""
    from apps.chat.services import engine

    answer = Message.objects.select_related("conversation__user").filter(pk=message_id).first()
    if answer is None:
        return

    try:
        engine.complete_turn(answer)
    except SoftTimeLimitExceeded:
        engine.fail_turn(answer, "The model took too long to answer. Try again.")
    except Exception:
        # Anything unexpected still has to resolve the reply, or the page
        # would wait on it until the timeout.
        logger.exception("Chat turn %s failed", message_id)
        engine.fail_turn(answer, "Something went wrong while generating this reply.")
        raise

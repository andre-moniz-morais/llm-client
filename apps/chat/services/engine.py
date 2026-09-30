"""Running a chat turn: build history, call KIE, store and notify.

A turn is split in two. :func:`send_message` runs in the request: it stores the
question and a pending reply and queues the model call. :func:`complete_turn`
runs on the Celery worker and fills that reply in, so a slow model never holds
a web worker. The page polls the pending reply until it resolves.

The frontend renders whatever HTML this stores, so a failure has to come back
as a styled fragment rather than an exception escaping to a 500 page.
"""

from __future__ import annotations

import logging
from html import escape

from django.db import transaction

from apps.catalog.services import catalog
from apps.common.services import html as html_service
from apps.common.services import kie, queue, telegram
from apps.chat.models import Attachment, Conversation, Message

from . import protocols
from .protocols import Turn

logger = logging.getLogger(__name__)

# How many previous turns to replay. Long threads are expensive and most models
# here have generous context, so this is a cost guard rather than a limit.
HISTORY_LIMIT = 30


class ChatError(Exception):
    """A chat turn could not be completed."""


def build_history(conversation: Conversation, limit: int = HISTORY_LIMIT) -> list[Turn]:
    """Replay a conversation as protocol-neutral turns."""
    messages = list(
        conversation.messages.filter(
            role__in=[Message.Role.USER, Message.Role.ASSISTANT],
            status=Message.Status.COMPLETE,
        ).order_by("-created_at")[:limit]
    )
    messages.reverse()

    turns = []
    for message in messages:
        image_urls = [
            item.get("url")
            for item in (message.attachments or [])
            if isinstance(item, dict) and item.get("url")
        ]
        text = message.content
        if not text and not image_urls:
            continue
        turns.append(Turn(role=message.role, text=text or "", image_urls=image_urls))
    return turns


def store_attachments(user, conversation_files) -> list[dict]:
    """Upload images to KIE and keep our own copy.

    Returns the descriptors stored on the message: KIE's URL is what the model
    reads, the local one is what the conversation shows once KIE's copy expires.
    """
    if not conversation_files:
        return []

    client = kie.client_for(user)
    stored = []
    for uploaded in conversation_files:
        content = uploaded.read()
        uploaded.seek(0)
        try:
            remote_url = client.upload_file(uploaded.name, content, upload_path="chat")
        except kie.KieError:
            logger.warning("Could not upload %s to KIE", uploaded.name, exc_info=True)
            continue

        attachment = Attachment.objects.create(
            user=user,
            file=uploaded,
            remote_url=remote_url,
            content_type=getattr(uploaded, "content_type", "") or "",
            size=getattr(uploaded, "size", 0) or 0,
        )
        stored.append(
            {
                "id": attachment.pk,
                "url": remote_url,
                "local_url": attachment.file.url,
                "name": uploaded.name,
            }
        )
    return stored


def send_message(
    *,
    user,
    conversation: Conversation,
    text: str,
    files=None,
) -> tuple[Message, Message]:
    """Run one turn and return the stored ``(question, answer)`` pair.

    Both messages are persisted whatever happens, so a failed turn stays visible
    in the thread instead of vanishing on reload.
    """
    spec = catalog.spec(conversation.model_slug)
    if spec is None:
        raise ChatError("That model is no longer listed in the catalogue. Pick another one.")
    if not spec.adapter.startswith("chat_"):
        raise ChatError(f"{spec.name} is not a chat model.")

    attachments = store_attachments(user, files or [])

    with transaction.atomic():
        question = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content=text,
            content_html=html_service.sanitize(html_service.text_to_html(text)),
            attachments=attachments,
        )
        conversation.title_from(text)
        conversation.model_id = spec.model_id
        conversation.model_name = spec.name
        conversation.save(update_fields=["title", "model_id", "model_name", "updated_at"])

    Attachment.objects.filter(
        pk__in=[item["id"] for item in attachments], message__isnull=True
    ).update(message=question)

    answer = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        status=Message.Status.PENDING,
    )

    from apps.chat import tasks

    queue.enqueue(tasks.complete_turn, answer.pk)
    # Without a worker the task already ran; with one this is still pending.
    answer.refresh_from_db()
    return question, answer


def complete_turn(answer: Message) -> Message:
    """Ask the model for the pending reply ``answer`` and store the outcome."""
    if not answer.is_pending:
        # A redelivered task, or one that lost the race with the timeout.
        return answer

    conversation = answer.conversation
    user = conversation.user

    spec = catalog.spec(conversation.model_slug)
    if spec is None:
        return fail_turn(answer, "That model is no longer listed in the catalogue. Pick another one.")

    # The pending reply itself is excluded: history only replays complete turns.
    turns = build_history(conversation)
    body = protocols.build_request(spec, turns, html_service.CHAT_SYSTEM_PROMPT)

    try:
        client = kie.client_for(user)
        payload = client.chat(spec.create_path, body)
    except kie.KieError as exc:
        return fail_turn(answer, exc.message)

    reply = protocols.read_reply(spec, payload)
    if not reply:
        return fail_turn(
            answer, "The model returned an empty response. Try again or pick another model."
        )

    answer.content = reply
    answer.content_html = html_service.fragment(html_service.normalize_reply(reply))
    answer.usage = protocols.read_usage(payload)
    answer.status = Message.Status.COMPLETE
    answer.save(update_fields=["content", "content_html", "usage", "status"])
    conversation.touch()
    notify(user, conversation, answer)
    return answer


def fail_turn(answer: Message, message: str) -> Message:
    """Resolve a pending reply as failed, keeping it visible in the thread."""
    answer.content = ""
    answer.content_html = html_service.error_fragment(message)
    answer.status = Message.Status.FAILED
    answer.error = message
    answer.save(update_fields=["content", "content_html", "status", "error"])
    return answer


def expire_if_stale(answer: Message) -> Message:
    """Give up on a reply the worker never delivered, so the page stops waiting."""
    if answer.is_stale:
        return fail_turn(answer, "The reply did not arrive in time. Try sending again.")
    return answer


def notify(user, conversation: Conversation, answer: Message) -> None:
    """Mirror a completed reply to Telegram, if the user connected a bot."""
    user_settings = getattr(user, "settings", None)
    if user_settings is None or not user_settings.telegram_active:
        return

    # Telegram parses the message as HTML, so everything interpolated into it
    # has to be escaped - the reply text especially, since it may discuss markup.
    body = escape(html_service.html_to_text(answer.content_html))
    title = escape(conversation.display_title)
    model = escape(conversation.model_name or conversation.model_slug)
    telegram.notify(user_settings, f"<b>{title}</b>\n<i>{model}</i>\n\n{body}")

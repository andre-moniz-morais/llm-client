"""The chat page and the HTML fragments it exchanges with the backend."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from apps.catalog.services import catalog
from apps.chat.models import Conversation
from apps.chat.services import engine
from apps.common.services import html as html_service
from apps.common.services import kie


def _fragment_response(html: str, status: int = 200) -> HttpResponse:
    return HttpResponse(html, content_type="text/html; charset=utf-8", status=status)


@login_required
def index(request: HttpRequest) -> HttpResponse:
    """The chat page, with the model list loaded for this visit."""
    conversation_id = request.GET.get("conversation")
    conversation = None
    if conversation_id:
        conversation = get_object_or_404(
            Conversation, pk=conversation_id, user=request.user, archived=False
        )

    preferred = request.GET.get("model") or (conversation.model_slug if conversation else "")
    preferred = preferred or request.user.settings.default_chat_model
    _, selected_slug = catalog.resolve_spec("chat", preferred)
    models = catalog.models_for("chat")

    return render(
        request,
        "pages/chat.html",
        {
            "category": "chat",
            "models": models,
            "providers": catalog.providers_for("chat"),
            "selected_slug": selected_slug,
            "conversation": conversation,
            "messages_list": conversation.messages.all() if conversation else [],
            "conversations": Conversation.objects.filter(user=request.user, archived=False)[:40],
            "has_key": request.user.settings.has_kie_key,
            "notify_on_reply": request.user.settings.browser_notifications_enabled,
        },
    )


@login_required
@require_POST
def send(request: HttpRequest) -> HttpResponse:
    """Run one turn and return the two rendered messages.

    The response is the HTML the page appends to the transcript, which keeps
    rendering in one place instead of duplicated in JavaScript.
    """
    text = (request.POST.get("message") or "").strip()
    files = request.FILES.getlist("images")
    if not text and not files:
        return _fragment_response(
            html_service.error_fragment("Type a message before sending."), status=400
        )

    conversation_id = request.POST.get("conversation")
    model_slug = request.POST.get("model") or ""

    if conversation_id:
        conversation = get_object_or_404(Conversation, pk=conversation_id, user=request.user)
        if model_slug and model_slug != conversation.model_slug:
            conversation.model_slug = model_slug
            conversation.save(update_fields=["model_slug", "updated_at"])
    else:
        spec = catalog.spec(model_slug)
        if spec is None:
            return _fragment_response(
                html_service.error_fragment("Pick a model before sending."), status=400
            )
        conversation = Conversation.objects.create(
            user=request.user,
            model_slug=spec.slug,
            model_id=spec.model_id,
            model_name=spec.name,
        )

    # Remember the model so the next visit opens on it.
    user_settings = request.user.settings
    if user_settings.default_chat_model != conversation.model_slug:
        user_settings.set_default_model_for("chat", conversation.model_slug)
        user_settings.save(update_fields=["default_chat_model", "updated_at"])

    try:
        question, answer = engine.send_message(
            user=request.user,
            conversation=conversation,
            text=text,
            files=files,
        )
    except kie.MissingApiKey as exc:
        return _fragment_response(html_service.error_fragment(exc.message), status=400)
    except engine.ChatError as exc:
        return _fragment_response(html_service.error_fragment(str(exc)), status=400)

    html = render_to_string(
        "partials/messages.html",
        {"messages_list": [question, answer], "conversation": conversation},
        request=request,
    )
    response = _fragment_response(html)
    # Only the id travels in a header; a title would carry arbitrary text, and
    # headers are limited to latin-1.
    response["X-Conversation-Id"] = str(conversation.pk)
    return response


@login_required
def conversation_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """A conversation's transcript, as a fragment for in-page navigation."""
    conversation = get_object_or_404(Conversation, pk=pk, user=request.user)
    html = render_to_string(
        "partials/messages.html",
        {"messages_list": conversation.messages.all(), "conversation": conversation},
        request=request,
    )
    response = _fragment_response(html)
    response["X-Conversation-Model"] = conversation.model_slug
    return response


@login_required
@require_POST
def archive(request: HttpRequest, pk: int) -> HttpResponse:
    conversation = get_object_or_404(Conversation, pk=pk, user=request.user)
    conversation.archived = True
    conversation.save(update_fields=["archived", "updated_at"])
    return redirect("chat:index")

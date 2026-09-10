"""API access to conversations and messages."""

from __future__ import annotations

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.catalog.services import catalog
from apps.chat.filters import ConversationFilter, MessageFilter
from apps.chat.models import Conversation, Message
from apps.chat.serializers import (
    ConversationDetailSerializer,
    ConversationSerializer,
    MessageSerializer,
)
from apps.chat.services import engine
from apps.common.services import kie


class ConversationViewSet(viewsets.ModelViewSet):
    """Conversations belonging to the signed-in user."""

    serializer_class = ConversationSerializer
    filterset_class = ConversationFilter
    search_fields = ["title", "model_name"]
    ordering_fields = ["updated_at", "created_at", "title"]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ConversationDetailSerializer
        return ConversationSerializer

    def perform_create(self, serializer):
        spec = catalog.spec(serializer.validated_data.get("model_slug", ""))
        if spec is None or not spec.adapter.startswith("chat_"):
            raise ValidationError({"model_slug": "Pick a chat model from the catalog."})
        serializer.save(user=self.request.user, model_id=spec.model_id, model_name=spec.name)

    @action(detail=True, methods=["post"], url_path="send")
    def send(self, request, pk=None):
        """Run one turn against the conversation's model."""
        conversation = self.get_object()
        text = (request.data.get("message") or "").strip()
        files = request.FILES.getlist("images") if hasattr(request, "FILES") else []
        if not text and not files:
            raise ValidationError("Send some text or at least one image.")

        try:
            question, answer = engine.send_message(
                user=request.user, conversation=conversation, text=text, files=files
            )
        except kie.MissingApiKey as exc:
            return Response({"detail": exc.message}, status=400)
        except engine.ChatError as exc:
            return Response({"detail": str(exc)}, status=400)

        return Response(
            {
                "conversation": ConversationSerializer(conversation).data,
                "question": MessageSerializer(question).data,
                "answer": MessageSerializer(answer).data,
            },
            status=201,
        )


class MessageViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Messages are created through a conversation's ``send`` action."""

    serializer_class = MessageSerializer
    filterset_class = MessageFilter
    search_fields = ["content"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        return Message.objects.filter(conversation__user=self.request.user)

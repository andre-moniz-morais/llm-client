"""Serializers for conversations and their messages."""

from __future__ import annotations

from rest_framework import serializers

from apps.chat.models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "role",
            "content",
            "content_html",
            "status",
            "error",
            "attachments",
            "usage",
            "created_at",
        ]
        read_only_fields = fields


class ConversationSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(source="messages.count", read_only=True)

    class Meta:
        model = Conversation
        fields = [
            "id",
            "title",
            "model_slug",
            "model_name",
            "model_id",
            "archived",
            "message_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["model_name", "model_id", "created_at", "updated_at"]


class ConversationDetailSerializer(ConversationSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta(ConversationSerializer.Meta):
        fields = ConversationSerializer.Meta.fields + ["messages"]


class SendMessageSerializer(serializers.Serializer):
    """The body of a chat turn posted through the API."""

    message = serializers.CharField(allow_blank=True, required=False, default="")
    images = serializers.ListField(child=serializers.ImageField(), required=False, default=list)

    def validate(self, attrs: dict) -> dict:
        if not attrs.get("message") and not attrs.get("images"):
            raise serializers.ValidationError("Send some text or at least one image.")
        return attrs

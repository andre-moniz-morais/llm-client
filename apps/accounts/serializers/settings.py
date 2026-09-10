"""Serializer for a user's own settings.

Secrets are write-only: the API accepts a new key but never hands one back.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.accounts.models import UserSettings


class UserSettingsSerializer(serializers.ModelSerializer):
    kie_api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    telegram_bot_token = serializers.CharField(write_only=True, required=False, allow_blank=True)

    has_kie_key = serializers.BooleanField(read_only=True)
    masked_kie_api_key = serializers.CharField(read_only=True)
    telegram_connected = serializers.BooleanField(read_only=True)

    class Meta:
        model = UserSettings
        fields = [
            "kie_api_key",
            "telegram_bot_token",
            "telegram_chat_id",
            "telegram_notifications_enabled",
            "browser_notifications_enabled",
            "default_chat_model",
            "default_image_model",
            "default_video_model",
            "default_music_model",
            "has_kie_key",
            "masked_kie_api_key",
            "telegram_connected",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def update(self, instance: UserSettings, validated_data: dict) -> UserSettings:
        # The plain-text properties handle encryption on assignment.
        for field in ("kie_api_key", "telegram_bot_token"):
            if field in validated_data:
                setattr(instance, field, validated_data.pop(field))
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance

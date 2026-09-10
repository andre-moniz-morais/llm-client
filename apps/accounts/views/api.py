"""API access to the signed-in user's settings."""

from __future__ import annotations

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import UserSettings
from apps.accounts.serializers import UserSettingsSerializer
from apps.common.services import kie


class UserSettingsViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """A user can only ever see and edit their own settings row."""

    serializer_class = UserSettingsSerializer

    def get_queryset(self):
        return UserSettings.objects.filter(user=self.request.user)

    def get_object(self) -> UserSettings:
        settings_row, _ = UserSettings.objects.get_or_create(user=self.request.user)
        return settings_row

    @action(detail=False, methods=["get", "put", "patch"], url_path="me")
    def me(self, request):
        instance = self.get_object()
        if request.method == "GET":
            return Response(self.get_serializer(instance).data)

        serializer = self.get_serializer(
            instance, data=request.data, partial=request.method == "PATCH"
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="verify-key")
    def verify_key(self, request):
        """Confirm the stored key works and report the credit balance."""
        try:
            client = kie.client_for(request.user)
            credits = client.credits()
        except kie.MissingApiKey as exc:
            return Response({"valid": False, "detail": exc.message}, status=400)
        except kie.KieError as exc:
            return Response({"valid": False, "detail": exc.message}, status=502)
        return Response({"valid": True, "credits": credits})

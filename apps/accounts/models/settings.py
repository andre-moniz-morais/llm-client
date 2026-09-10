"""Per-user configuration: credentials and how the user is notified."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.services import crypto


class UserSettings(models.Model):
    """Everything a user configures on the settings page.

    Secrets are held in ``*_encrypted`` columns and read through plain-text
    properties, so callers never deal with ciphertext.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="settings",
    )

    kie_api_key_encrypted = models.TextField(blank=True, default="")
    telegram_bot_token_encrypted = models.TextField(blank=True, default="")
    telegram_chat_id = models.CharField(max_length=64, blank=True, default="")
    telegram_notifications_enabled = models.BooleanField(default=True)

    # A desktop notification is raised by the page itself, so this is only the
    # user's preference - the browser's own permission grant is what actually
    # allows it, and that is per-browser rather than per-account.
    browser_notifications_enabled = models.BooleanField(default=False)

    default_chat_model = models.CharField(max_length=200, blank=True, default="")
    default_image_model = models.CharField(max_length=200, blank=True, default="")
    default_video_model = models.CharField(max_length=200, blank=True, default="")
    default_music_model = models.CharField(max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "user settings"
        verbose_name_plural = "user settings"

    def __str__(self) -> str:
        return f"Settings for {self.user}"

    # -- secrets ---------------------------------------------------------- #

    @property
    def kie_api_key(self) -> str:
        return crypto.decrypt(self.kie_api_key_encrypted)

    @kie_api_key.setter
    def kie_api_key(self, value: str) -> None:
        self.kie_api_key_encrypted = crypto.encrypt((value or "").strip())

    @property
    def telegram_bot_token(self) -> str:
        return crypto.decrypt(self.telegram_bot_token_encrypted)

    @telegram_bot_token.setter
    def telegram_bot_token(self, value: str) -> None:
        self.telegram_bot_token_encrypted = crypto.encrypt((value or "").strip())

    # -- derived state ---------------------------------------------------- #

    @property
    def has_kie_key(self) -> bool:
        return bool(self.kie_api_key_encrypted)

    @property
    def masked_kie_api_key(self) -> str:
        return crypto.mask(self.kie_api_key)

    @property
    def masked_telegram_bot_token(self) -> str:
        return crypto.mask(self.telegram_bot_token)

    @property
    def telegram_connected(self) -> bool:
        return bool(self.telegram_bot_token_encrypted and self.telegram_chat_id)

    @property
    def telegram_active(self) -> bool:
        """Whether a completed response should be pushed to Telegram."""
        return self.telegram_connected and self.telegram_notifications_enabled

    def default_model_for(self, category: str) -> str:
        return getattr(self, f"default_{category}_model", "") or ""

    def set_default_model_for(self, category: str, slug: str) -> None:
        attribute = f"default_{category}_model"
        if hasattr(self, attribute):
            setattr(self, attribute, slug)

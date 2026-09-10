"""Persisted chat conversations and their messages."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    """A chat thread against one model."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    title = models.CharField(max_length=200, blank=True, default="")
    model_slug = models.CharField(max_length=200)
    model_name = models.CharField(max_length=200, blank=True, default="")
    model_id = models.CharField(max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [models.Index(fields=["user", "-updated_at"])]

    def __str__(self) -> str:
        return self.title or f"Conversation {self.pk}"

    @property
    def display_title(self) -> str:
        return self.title or "New conversation"

    def touch(self) -> None:
        self.updated_at = timezone.now()
        self.save(update_fields=["updated_at"])

    def title_from(self, text: str, *, limit: int = 60) -> None:
        """Name an untitled conversation after its opening message."""
        if self.title or not text:
            return
        cleaned = " ".join(text.split())
        self.title = cleaned[:limit] + ("…" if len(cleaned) > limit else "")


class Message(models.Model):
    """One turn in a conversation.

    ``content`` holds what the user typed or what the model returned verbatim;
    ``content_html`` holds the sanitised fragment the frontend renders.
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    class Status(models.TextChoices):
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField(blank=True, default="")
    content_html = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.COMPLETE)
    error = models.TextField(blank=True, default="")

    attachments = models.JSONField(default=list, blank=True)
    usage = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")
        indexes = [models.Index(fields=["conversation", "created_at"])]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:40]}"

    @property
    def is_user(self) -> bool:
        return self.role == self.Role.USER


class Attachment(models.Model):
    """An image a user attached to a chat message.

    Files uploaded to KIE expire after 24 hours, so the local copy is what makes
    a conversation still readable later.
    """

    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="files",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_attachments",
    )
    file = models.FileField(upload_to="attachments/%Y/%m/")
    remote_url = models.URLField(max_length=1000, blank=True, default="")
    content_type = models.CharField(max_length=100, blank=True, default="")
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self) -> str:
        return self.file.name

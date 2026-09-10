"""Persisted image, video and music generations."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Generation(models.Model):
    """One generation request and its outcome.

    KIE tasks are asynchronous: creating one yields a task id that has to be
    polled.  A row is written as soon as the task is accepted so nothing is lost
    if the browser goes away mid-render.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    class Category(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        MUSIC = "music", "Music"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="generations",
    )
    category = models.CharField(max_length=16, choices=Category.choices)

    model_slug = models.CharField(max_length=200)
    model_id = models.CharField(max_length=200, blank=True, default="")
    model_name = models.CharField(max_length=200, blank=True, default="")
    adapter = models.CharField(max_length=32, default="jobs")
    # Where to ask about this task. Recorded at creation so a running task can
    # still be polled if the catalog changes underneath it.
    poll_path = models.CharField(max_length=200, blank=True, default="")

    prompt = models.TextField(blank=True, default="")
    parameters = models.JSONField(default=dict, blank=True)
    reference_images = models.JSONField(default=list, blank=True)

    task_id = models.CharField(max_length=200, blank=True, default="", db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True, default="")
    raw_result = models.JSONField(default=dict, blank=True)

    notified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "category", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.model_name or self.model_slug} ({self.status})"

    @property
    def is_finished(self) -> bool:
        return self.status in {self.Status.SUCCEEDED, self.Status.FAILED}

    @property
    def is_stale(self) -> bool:
        """Whether a task has been running longer than we are willing to wait."""
        if self.is_finished:
            return False
        age = (timezone.now() - self.created_at).total_seconds()
        return age > settings.GENERATION_TIMEOUT_SECONDS

    def mark_succeeded(self, payload: dict | None = None) -> None:
        self.status = self.Status.SUCCEEDED
        self.completed_at = timezone.now()
        if payload is not None:
            self.raw_result = payload

    def mark_failed(self, message: str, payload: dict | None = None) -> None:
        self.status = self.Status.FAILED
        self.error = message
        self.completed_at = timezone.now()
        if payload is not None:
            self.raw_result = payload

    @property
    def short_prompt(self) -> str:
        cleaned = " ".join(self.prompt.split())
        return cleaned[:80] + ("…" if len(cleaned) > 80 else "")


class Asset(models.Model):
    """A file produced by a generation.

    KIE deletes generated media after 14 days, so each result is downloaded and
    stored locally; ``remote_url`` is kept only for reference.
    """

    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        OTHER = "other", "Other"

    generation = models.ForeignKey(
        Generation,
        on_delete=models.CASCADE,
        related_name="assets",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.IMAGE)
    file = models.FileField(upload_to="generations/%Y/%m/", blank=True)
    remote_url = models.URLField(max_length=1000, blank=True, default="")
    thumbnail_url = models.URLField(max_length=1000, blank=True, default="")
    title = models.CharField(max_length=200, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")

    def __str__(self) -> str:
        return self.title or self.file.name or self.remote_url

    @property
    def url(self) -> str:
        """Prefer our own copy; fall back to KIE's URL until the download lands."""
        if self.file:
            return self.file.url
        return self.remote_url

    @property
    def is_playable(self) -> bool:
        return self.kind in {self.Kind.VIDEO, self.Kind.AUDIO}

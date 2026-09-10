"""Reading generation results out of KIE's task payloads.

Task status responses are not uniform: the unified jobs endpoint nests results
under ``resultJson``, Suno returns a list of tracks, Veo and Runway each have
their own shape, and result URLs appear under a dozen different keys.  This
module reduces all of that to a status and a list of media URLs.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

# KIE reports state through several differently-spelled fields.
SUCCESS_STATES = {"success", "succeeded", "completed", "complete", "finished", "done", "1"}
FAILURE_STATES = {"fail", "failed", "error", "cancelled", "canceled", "2", "3"}
RUNNING_STATES = {"running", "processing", "generating", "in_progress", "progress"}
PENDING_STATES = {"waiting", "queuing", "queued", "pending", "created", "0"}

# Keys that hold result URLs, anywhere in the payload.
URL_KEYS = {
    "resultUrls", "result_urls", "resultUrl", "result_url",
    "imageUrls", "image_urls", "imageUrl", "image_url",
    "videoUrl", "video_url", "videoUrls", "video_urls",
    "audioUrl", "audio_url", "audio_urls", "streamAudioUrl", "source_audio_url",
    "url", "urls", "downloadUrl", "download_url", "originUrl",
    "output", "outputs", "files",
}

THUMBNAIL_KEYS = {"imageUrl", "image_url", "coverImageUrl", "thumbnailUrl", "thumbnail_url"}

EXTENSION_KINDS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".svg"},
    "video": {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"},
    "audio": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"},
}

URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)


@dataclass(slots=True)
class Media:
    url: str
    kind: str = "image"
    title: str = ""
    thumbnail_url: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class TaskResult:
    status: str
    media: list[Media] = field(default_factory=list)
    error: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_finished(self) -> bool:
        return self.status in {SUCCEEDED, FAILED}


def kind_for_url(url: str, fallback: str = "other") -> str:
    """Guess a media type from a URL's extension."""
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    for kind, extensions in EXTENSION_KINDS.items():
        if any(path.endswith(extension) for extension in extensions):
            return kind
    return fallback


def _maybe_json(value):
    """Several endpoints return nested JSON as a string."""
    if isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _collect_urls(node, found: list[str], depth: int = 0) -> None:
    """Walk a payload gathering result URLs in document order."""
    if depth > 8:
        return

    node = _maybe_json(node)

    if isinstance(node, str):
        if node.startswith(("http://", "https://")):
            found.append(node)
        return

    if isinstance(node, list):
        for item in node:
            _collect_urls(item, found, depth + 1)
        return

    if isinstance(node, dict):
        for key, value in node.items():
            value = _maybe_json(value)
            if key in URL_KEYS:
                _collect_urls(value, found, depth + 1)
            elif isinstance(value, (dict, list)):
                _collect_urls(value, found, depth + 1)


def _normalize_state(payload: dict) -> str:
    for key in ("state", "status", "successFlag", "taskStatus", "flag"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip().lower()
        if text in SUCCESS_STATES:
            return SUCCEEDED
        if text in FAILURE_STATES:
            return FAILED
        if text in RUNNING_STATES:
            return RUNNING
        if text in PENDING_STATES:
            return PENDING
    return ""


def _error_message(payload: dict) -> str:
    for key in ("failMsg", "errorMessage", "error_message", "msg", "message", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip() and value.strip().lower() != "success":
            return value.strip()
    return ""


def _tracks(payload: dict) -> list[Media]:
    """Suno returns a list of tracks, each with its own audio and cover art."""
    candidates = payload.get("data")
    if isinstance(candidates, dict):
        candidates = candidates.get("data") or candidates.get("tracks")
    if not isinstance(candidates, list):
        return []

    media = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = (
            item.get("audioUrl")
            or item.get("audio_url")
            or item.get("streamAudioUrl")
            or item.get("source_audio_url")
        )
        if not url:
            continue
        media.append(
            Media(
                url=url,
                kind="audio",
                title=item.get("title") or "",
                thumbnail_url=item.get("imageUrl") or item.get("image_url") or "",
                metadata={
                    key: item[key]
                    for key in ("id", "duration", "tags", "modelName", "prompt")
                    if key in item
                },
            )
        )
    return media


def parse(payload: dict, *, category: str = "image") -> TaskResult:
    """Turn a task-status payload into a status plus the media it produced."""
    if not isinstance(payload, dict):
        return TaskResult(status=RUNNING, raw={})

    # The jobs endpoint keeps the interesting parts one level down.
    inner = payload
    for key in ("data", "response"):
        candidate = _maybe_json(payload.get(key))
        if isinstance(candidate, dict):
            inner = {**candidate, **{k: v for k, v in payload.items() if k not in {key}}}
            break

    state = _normalize_state(inner) or _normalize_state(payload)

    media = _tracks(inner)
    if not media:
        urls: list[str] = []
        _collect_urls(inner.get("resultJson") or inner.get("result") or inner, urls)
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            media.append(Media(url=url, kind=kind_for_url(url, fallback=category)))

    error = _error_message(inner)

    if not state:
        state = SUCCEEDED if media else RUNNING
    if state == SUCCEEDED and not media:
        # A "success" with nothing attached is a failure as far as the user is
        # concerned; say so rather than showing an empty result card.
        return TaskResult(
            status=FAILED,
            error=error or "The task finished but returned no files.",
            raw=payload,
        )
    if state == FAILED and not error:
        error = "The model could not complete this generation."

    return TaskResult(status=state, media=media, error=error, raw=payload)

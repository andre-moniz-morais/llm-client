"""Starting, polling and storing generations.

KIE generation is asynchronous everywhere: a request returns a task id and the
result arrives later.  The frontend polls :func:`refresh` until the task
finishes, at which point the produced files are downloaded into local storage -
KIE deletes them after 14 days.
"""

from __future__ import annotations

import logging
import mimetypes
from urllib.parse import urlparse

import requests
from django.core.files.base import ContentFile
from django.db import transaction

from apps.catalog.services import catalog
from apps.catalog.services.docs import ModelSpec
from apps.common.services import html as html_service
from apps.common.services import kie, telegram
from apps.generation.models import Asset, Generation

from . import results
from .inputs import build_input

logger = logging.getLogger(__name__)

# Generated files are fetched server-side; anything larger is left as a link.
MAX_ASSET_BYTES = 100 * 1024 * 1024
DOWNLOAD_TIMEOUT = 120


class GenerationError(Exception):
    """A generation could not be started."""


def start(*, user, spec: ModelSpec, form_data: dict, files=None) -> Generation:
    """Submit a generation and record it.

    Reference images are uploaded to KIE first, because every generation
    endpoint takes image inputs as URLs rather than file content.
    """
    client = kie.client_for(user)

    image_urls = []
    for uploaded in files or []:
        content = uploaded.read()
        uploaded.seek(0)
        image_urls.append(
            client.upload_file(uploaded.name, content, upload_path=spec.category)
        )

    model_input, prompt = build_input(spec, form_data, image_urls)

    generation = Generation.objects.create(
        user=user,
        category=spec.category if spec.category in Generation.Category.values else "image",
        model_slug=spec.slug,
        model_id=spec.model_id,
        model_name=spec.name,
        adapter=spec.adapter,
        poll_path=spec.poll_path,
        prompt=prompt,
        parameters=model_input,
        reference_images=image_urls,
    )

    try:
        if spec.adapter == "jobs":
            task_id = client.create_job(spec.model_id, model_input)
        else:
            task_id = client.create_task(spec.create_path, model_input)
    except kie.KieError as exc:
        generation.mark_failed(exc.message, payload=exc.payload if isinstance(exc.payload, dict) else {})
        generation.save()
        return generation

    generation.task_id = task_id
    generation.status = Generation.Status.RUNNING
    generation.save(update_fields=["task_id", "status", "updated_at"])
    return generation


def refresh(generation: Generation) -> Generation:
    """Poll a running generation once and persist any change."""
    if generation.is_finished:
        return generation

    if generation.is_stale:
        generation.mark_failed("The task did not finish in time.")
        generation.save()
        return generation

    if not generation.task_id:
        return generation

    try:
        client = kie.client_for(generation.user)
        if generation.adapter == "jobs":
            payload = client.job_status(generation.task_id)
        else:
            payload = client.task_status(generation.poll_path, generation.task_id)
    except kie.KieError as exc:
        # A transient polling failure should not kill a task that may still be
        # running; only give up once it goes stale.
        logger.info("Could not poll task %s: %s", generation.task_id, exc)
        return generation

    outcome = results.parse(payload, category=generation.category)

    if outcome.status == results.FAILED:
        generation.mark_failed(outcome.error, payload=outcome.raw)
        generation.save()
        notify(generation)
        return generation

    if outcome.status != results.SUCCEEDED:
        if generation.status != Generation.Status.RUNNING:
            generation.status = Generation.Status.RUNNING
            generation.save(update_fields=["status", "updated_at"])
        return generation

    with transaction.atomic():
        generation.mark_succeeded(outcome.raw)
        generation.save()
        store_assets(generation, outcome.media)

    notify(generation)
    return generation


def store_assets(generation: Generation, media: list[results.Media]) -> list[Asset]:
    """Record each produced file, downloading a local copy where possible."""
    stored = []
    for item in media:
        asset = Asset.objects.create(
            generation=generation,
            kind=item.kind if item.kind in Asset.Kind.values else Asset.Kind.OTHER,
            remote_url=item.url,
            thumbnail_url=item.thumbnail_url,
            title=item.title,
            metadata=item.metadata,
        )
        download(asset)
        stored.append(asset)
    return stored


def download(asset: Asset) -> bool:
    """Copy a generated file into local storage.

    Failure is not fatal: ``Asset.url`` falls back to KIE's URL, which stays
    valid for 14 days.
    """
    if asset.file or not asset.remote_url:
        return False

    try:
        response = requests.get(asset.remote_url, timeout=DOWNLOAD_TIMEOUT, stream=True)
        response.raise_for_status()

        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_ASSET_BYTES:
            logger.info("Skipping oversized asset %s (%s bytes)", asset.remote_url, length)
            return False

        content = bytearray()
        for chunk in response.iter_content(chunk_size=256 * 1024):
            content += chunk
            if len(content) > MAX_ASSET_BYTES:
                logger.info("Skipping oversized asset %s", asset.remote_url)
                return False
    except (requests.RequestException, ValueError):
        logger.warning("Could not download %s", asset.remote_url, exc_info=True)
        return False

    asset.file.save(_filename_for(asset, response), ContentFile(bytes(content)), save=True)
    return True


def _filename_for(asset: Asset, response) -> str:
    name = urlparse(asset.remote_url).path.rsplit("/", 1)[-1] or f"asset-{asset.pk}"
    if "." not in name:
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        extension = mimetypes.guess_extension(content_type) or ""
        name = f"{name}{extension}"
    return name[:120]


def notify(generation: Generation) -> None:
    """Tell the user on Telegram that a generation finished."""
    user_settings = getattr(generation.user, "settings", None)
    if user_settings is None or not user_settings.telegram_active or generation.notified:
        return

    from html import escape

    model = escape(generation.model_name or generation.model_slug)
    if generation.status == Generation.Status.SUCCEEDED:
        lines = [f"✅ <b>{model}</b> finished", escape(generation.short_prompt)]
        lines += [escape(asset.remote_url) for asset in generation.assets.all()[:4]]
    else:
        lines = [f"⚠️ <b>{model}</b> failed", escape(generation.error or "Unknown error")]

    if telegram.notify(user_settings, "\n".join(line for line in lines if line)):
        generation.notified = True
        generation.save(update_fields=["notified"])


def render(generation: Generation) -> str:
    """The HTML fragment the frontend drops into the results area.

    This is our own template, so it is trusted markup; every value the model or
    the user supplied is escaped by the template engine on the way in.
    """
    from django.template.loader import render_to_string

    return render_to_string("partials/generation.html", {"generation": generation})


def spec_for(generation: Generation) -> ModelSpec | None:
    return catalog.spec(generation.model_slug)

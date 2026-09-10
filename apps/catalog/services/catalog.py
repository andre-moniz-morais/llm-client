"""The model catalog the UI is built from.

Model lists are pulled from the live documentation when a category page is
opened, and a model's request schema is pulled when that model is selected.
Both are memoised in Django's cache, and a snapshot shipped with the source
tree keeps the site usable when docs.kie.ai is unreachable.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

from . import docs
from .docs import CATEGORIES, DocsUnavailable, ModelRef, ModelSpec

logger = logging.getLogger(__name__)

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog.json"

INDEX_CACHE_KEY = "catalog:index:v1"
SPEC_CACHE_KEY = "catalog:spec:v1:{slug}"

CATEGORY_LABELS = {
    "chat": "Chat",
    "image": "Image generation",
    "video": "Video generation",
    "music": "Music & audio",
}

CATEGORY_ICONS = {
    "chat": "chat",
    "image": "image",
    "video": "video",
    "music": "music",
}


@lru_cache(maxsize=1)
def _snapshot() -> dict[str, dict]:
    """The bundled catalog, keyed by slug."""
    try:
        rows = json.loads(SNAPSHOT_PATH.read_text())
    except (OSError, ValueError):
        logger.exception("Could not read the bundled catalog snapshot")
        return {}
    return {row["slug"]: row for row in rows if row.get("slug")}


def _snapshot_refs() -> list[ModelRef]:
    refs = [
        ModelRef(
            slug=row["slug"],
            name=row.get("name") or row["slug"],
            category=row.get("category") or "image",
            provider=row.get("provider") or "KIE",
            doc_url=row.get("doc_url") or "",
            summary=row.get("description", "")[:160],
        )
        for row in _snapshot().values()
    ]
    return sorted(refs, key=lambda r: (r.provider.lower(), r.name.lower()))


def index(refresh: bool = False) -> list[ModelRef]:
    """Every documented model, fetched on demand and cached.

    Falls back to the bundled snapshot if the documentation cannot be read, so
    a network hiccup degrades freshness rather than breaking the page.
    """
    if not refresh:
        cached = cache.get(INDEX_CACHE_KEY)
        if cached is not None:
            return [ModelRef(**row) for row in cached]

    try:
        refs = docs.fetch_index()
    except DocsUnavailable:
        logger.warning("Falling back to the bundled catalog snapshot", exc_info=True)
        return _snapshot_refs()

    if not refs:
        return _snapshot_refs()

    cache.set(INDEX_CACHE_KEY, [ref.as_dict() for ref in refs], settings.CATALOG_INDEX_TTL)
    return refs


def models_for(category: str, refresh: bool = False) -> list[ModelRef]:
    """The models in one category, loaded when that category's page opens."""
    return [ref for ref in index(refresh=refresh) if ref.category == category]


def providers_for(category: str) -> list[str]:
    seen = {ref.provider for ref in models_for(category) if ref.provider}
    return sorted(seen, key=str.lower)


def find_ref(slug: str) -> ModelRef | None:
    for ref in index():
        if ref.slug == slug:
            return ref

    row = _snapshot().get(slug)
    if row:
        return ModelRef(
            slug=row["slug"],
            name=row.get("name") or slug,
            category=row.get("category") or "image",
            provider=row.get("provider") or "KIE",
            doc_url=row.get("doc_url") or "",
            summary=row.get("description", "")[:160],
        )
    return None


def spec(slug: str, refresh: bool = False) -> ModelSpec | None:
    """A model's full request contract, fetched the first time it is selected."""
    key = SPEC_CACHE_KEY.format(slug=slug)
    if not refresh:
        cached = cache.get(key)
        if cached is not None:
            return ModelSpec.from_dict(cached)

    ref = find_ref(slug)
    if ref is None:
        return None

    parsed: ModelSpec | None = None
    if ref.doc_url:
        try:
            for candidate in docs.fetch_specs(ref):
                cache.set(
                    SPEC_CACHE_KEY.format(slug=candidate.slug),
                    candidate.as_dict(),
                    settings.CATALOG_SPEC_TTL,
                )
                if candidate.slug == slug:
                    parsed = candidate
        except DocsUnavailable:
            logger.warning("Could not read the spec for %s", slug, exc_info=True)

    if parsed is None:
        row = _snapshot().get(slug)
        if row is None:
            return None
        parsed = ModelSpec.from_dict(row)
        cache.set(key, parsed.as_dict(), settings.CATALOG_SPEC_TTL)

    return parsed


def resolve_spec(category: str, preferred: str = "", *, attempts: int = 5):
    """Pick the model a category page opens on, and load its schema.

    An explicit choice always wins.  Otherwise the first model alphabetically
    is often a poor greeting - the music list opens on an audio-isolation
    utility - so we prefer one that takes a text prompt, which is what someone
    arriving on the page is looking for.  Pages that cannot be parsed are
    skipped rather than shown as an empty form.
    """
    models = models_for(category)
    if not models:
        return None, ""

    if preferred:
        for ref in models:
            if ref.slug == preferred:
                found = spec(preferred)
                if found is not None:
                    return found, preferred
                break

    fallback = None
    for ref in models[:attempts]:
        found = spec(ref.slug)
        if found is None or found.category != category:
            continue
        if found.prompt_field:
            return found, ref.slug
        fallback = fallback or (found, ref.slug)

    if fallback:
        return fallback

    return None, models[0].slug


def categories() -> list[dict]:
    return [
        {"key": key, "label": CATEGORY_LABELS[key], "icon": CATEGORY_ICONS[key]}
        for key in CATEGORIES
    ]

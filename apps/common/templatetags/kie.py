"""Template helpers for rendering model-driven forms."""

from __future__ import annotations

import json

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# Fields shown above the fold. Everything else goes behind "Advanced options",
# because a model can declare a dozen knobs a user rarely touches.
PRIMARY_FIELDS = {
    "prompt", "text", "negative_prompt", "lyrics", "style", "title",
    "aspect_ratio", "resolution", "duration", "image_size", "voice",
    "customMode", "instrumental", "model",
}


def _is_primary(field: dict) -> bool:
    return bool(field.get("required")) or field.get("name") in PRIMARY_FIELDS


@register.filter
def primary_fields(spec) -> list[dict]:
    """The fields rendered directly in the form."""
    return [field for field in getattr(spec, "fields", []) if _is_primary(field)]


@register.filter
def advanced_fields(spec) -> list[dict]:
    """The fields tucked behind the advanced disclosure."""
    return [field for field in getattr(spec, "fields", []) if not _is_primary(field)]


@register.filter
def widget_for(field: dict) -> str:
    """Which input to draw for a schema field."""
    if field.get("enum"):
        return "select"
    kind = field.get("type")
    if kind == "boolean":
        return "checkbox"
    if kind in {"integer", "number"}:
        minimum, maximum = field.get("minimum"), field.get("maximum")
        if minimum is not None and maximum is not None:
            return "range"
        return "number"
    if kind == "array":
        return "textarea"
    if kind == "object":
        return "json"
    name = (field.get("name") or "").lower()
    if name in {"prompt", "text", "lyrics", "negative_prompt"} or (field.get("max_length") or 0) > 300:
        return "textarea"
    return "text"


@register.filter
def step_for(field: dict) -> str:
    """A sensible input step for a numeric field."""
    if field.get("type") == "integer":
        return "1"
    minimum, maximum = field.get("minimum"), field.get("maximum")
    if isinstance(minimum, (int, float)) and isinstance(maximum, (int, float)):
        span = float(maximum) - float(minimum)
        if span <= 2:
            return "0.05"
        if span <= 20:
            return "0.1"
    return "any"


@register.filter
def default_str(field: dict) -> str:
    """A field's default, rendered for an input's ``value``."""
    value = field.get("default")
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else ""
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


@register.filter
def as_json(value) -> str:
    """Serialise a value for embedding in a data attribute."""
    return mark_safe(json.dumps(value))


@register.filter
def kind_icon(category: str) -> str:
    return {"image": "image", "video": "video", "music": "music"}.get(category, "spark")

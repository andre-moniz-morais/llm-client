"""Turning a submitted form into the ``input`` object a KIE model expects.

The form itself is generated from the model's OpenAPI schema, so this is where
the round trip closes: each declared field is read back out of the POST data,
coerced to its declared type, and dropped if the user left it alone.
"""

from __future__ import annotations

import json
from typing import Any

TRUE_VALUES = {"1", "true", "yes", "on"}

# Fields that take a list of image URLs rather than a single one.
LIST_IMAGE_FIELDS = {
    "image_urls",
    "input_urls",
    "images",
    "input_image_urls",
    "reference_image_urls",
    "filesUrl",
}


def coerce(field: dict, raw: Any) -> Any:
    """Convert one submitted value to the type the schema declares."""
    kind = field.get("type") or "string"

    if kind == "boolean":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in TRUE_VALUES

    if kind in {"integer", "number"}:
        text = str(raw).strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
        if kind == "integer":
            return int(value)
        # Keep whole numbers whole so payloads read as the docs show them.
        return int(value) if value.is_integer() else value

    if kind == "array":
        if isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            text = str(raw).strip()
            if not text:
                return None
            if text.startswith("["):
                try:
                    items = json.loads(text)
                except ValueError:
                    items = [line.strip() for line in text.splitlines()]
            else:
                items = [line.strip() for line in text.splitlines()]
        items = [item for item in items if item not in ("", None)]
        return items or None

    if kind == "object":
        text = raw if isinstance(raw, str) else json.dumps(raw)
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    text = str(raw).strip()
    return text or None


def _apply_images(spec, model_input: dict, image_urls: list[str]) -> None:
    """Attach uploaded reference images to whichever field accepts them."""
    if not image_urls or not spec.image_field:
        return

    name = spec.image_field
    field = next((f for f in spec.fields if f.get("name") == name), {})
    is_list = field.get("type") == "array" or name in LIST_IMAGE_FIELDS

    if is_list:
        existing = model_input.get(name)
        existing = list(existing) if isinstance(existing, list) else []
        limit = field.get("max_items") or 10
        model_input[name] = (existing + image_urls)[:limit]
    else:
        model_input[name] = image_urls[0]


def build_input(spec, form_data, image_urls: list[str] | None = None) -> tuple[dict, str]:
    """Build the request body for a model, and return it with the prompt text.

    ``form_data`` is a ``QueryDict`` or plain mapping; only fields the model
    declares are read from it, so nothing extra can be smuggled into the call.
    """
    model_input: dict[str, Any] = {}

    for field in spec.fields:
        name = field.get("name")
        if not name:
            continue

        if field.get("type") == "array" and hasattr(form_data, "getlist"):
            values = [value for value in form_data.getlist(name) if value not in ("", None)]
            raw: Any = values if len(values) > 1 else (values[0] if values else "")
        else:
            raw = form_data.get(name, "")

        if field.get("type") == "boolean":
            # An unchecked box is absent from the POST body; only send a value
            # when the model declares a default we would be overriding.
            present = name in form_data
            if not present and field.get("default") in (None, False):
                continue
            model_input[name] = present
            continue

        if raw in ("", None):
            continue

        value = coerce(field, raw)
        if value is None:
            continue
        model_input[name] = value

    _apply_images(spec, model_input, image_urls or [])

    prompt = ""
    if spec.prompt_field:
        prompt = str(model_input.get(spec.prompt_field) or "")

    return model_input, prompt


def missing_required(spec, model_input: dict) -> list[str]:
    """Required fields the user did not fill in, by label."""
    missing = []
    for field in spec.fields:
        if not field.get("required"):
            continue
        value = model_input.get(field["name"])
        if value in (None, "", [], {}):
            missing.append(field.get("label") or field["name"])
    return missing

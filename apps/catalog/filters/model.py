"""Filtering for the catalog.

The catalog is not a queryset, so django-filter does not apply; these are plain
functions over the parsed model list.
"""

from __future__ import annotations

from apps.catalog.services.docs import ModelRef


def filter_models(models: list[ModelRef], params) -> list[ModelRef]:
    """Narrow a model list by category, provider and free-text search."""
    category = (params.get("category") or "").strip().lower()
    provider = (params.get("provider") or "").strip().lower()
    search = (params.get("search") or params.get("q") or "").strip().lower()

    results = models
    if category:
        results = [model for model in results if model.category == category]
    if provider:
        results = [model for model in results if model.provider.lower() == provider]
    if search:
        results = [
            model
            for model in results
            if search in model.name.lower()
            or search in model.provider.lower()
            or search in model.summary.lower()
            or search in model.slug
        ]
    return results

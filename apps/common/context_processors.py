"""Template context shared by every page."""

from __future__ import annotations

from apps.catalog.services import catalog

# The sidebar's sections, in order.  Kept here rather than in the template so
# the active-state logic has something to compare against.
NAV_SECTIONS = [
    {"key": "chat", "label": "Chat", "url_name": "chat:index", "icon": "chat"},
    {"key": "image", "label": "Image", "url_name": "generation:image", "icon": "image"},
    {"key": "video", "label": "Video", "url_name": "generation:video", "icon": "video"},
    {"key": "music", "label": "Music", "url_name": "generation:music", "icon": "music"},
    {"key": "gallery", "label": "Library", "url_name": "generation:gallery", "icon": "gallery"},
]


def navigation(request) -> dict:
    return {
        "nav_sections": NAV_SECTIONS,
        "category_labels": catalog.CATEGORY_LABELS,
        "site_name": "CRAFT",
    }

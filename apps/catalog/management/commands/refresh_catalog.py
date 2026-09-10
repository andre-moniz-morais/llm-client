"""Warm the catalog caches, and optionally rewrite the bundled snapshot."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.catalog.services import catalog
from apps.catalog.services.docs import DocsUnavailable


class Command(BaseCommand):
    help = "Fetch the KIE.ai model catalog and warm the cache."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--specs",
            action="store_true",
            help="Also fetch every model's request schema (slow; one request per model).",
        )

    def handle(self, *args, **options) -> None:
        try:
            models = catalog.index(refresh=True)
        except DocsUnavailable as exc:
            self.stderr.write(self.style.ERROR(f"Could not read the documentation: {exc}"))
            return

        counts: dict[str, int] = {}
        for model in models:
            counts[model.category] = counts.get(model.category, 0) + 1

        summary = ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))
        self.stdout.write(self.style.SUCCESS(f"Indexed {len(models)} models ({summary})."))

        if not options["specs"]:
            return

        loaded = 0
        for model in models:
            if catalog.spec(model.slug, refresh=True) is not None:
                loaded += 1
        self.stdout.write(self.style.SUCCESS(f"Cached {loaded} model schemas."))

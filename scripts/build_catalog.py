"""Refresh the catalog snapshot shipped in ``apps/catalog/data/catalog.json``.

The running site reads the model catalog from docs.kie.ai on demand; this
snapshot is only the offline fallback, so it needs regenerating whenever KIE
publishes models you want available without network access.

    python scripts/build_catalog.py [--out apps/catalog/data/catalog.json]

The docs host serves its Markdown through a flaky edge cache, so pages are
cached on disk and re-running the script fills in whatever it missed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.catalog.services import docs  # noqa: E402


def cached_fetch(url: str, cache_dir: Path | None) -> str:
    if cache_dir is None:
        return docs.fetch(url)

    name = re.sub(r"[^A-Za-z0-9]+", "_", url.removeprefix(f"{docs.DOCS_ROOT}/")) + ".md"
    path = cache_dir / name
    if path.exists() and path.stat().st_size:
        return path.read_text(errors="replace")

    body = docs.fetch(url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return body


def build(cache_dir: Path | None) -> list[dict]:
    index = cached_fetch(docs.INDEX_URL, cache_dir)
    refs = docs.parse_index(index)
    print(f"Indexed {len(refs)} models; reading their specifications...", file=sys.stderr)

    specs: dict[str, dict] = {}
    failures = 0
    for ref in refs:
        try:
            markdown = cached_fetch(ref.doc_url, cache_dir)
        except docs.DocsUnavailable as exc:
            print(f"  skipped {ref.doc_url}: {exc}", file=sys.stderr)
            failures += 1
            continue
        for spec in docs.parse_specs(markdown, ref):
            specs.setdefault(spec.slug, spec.as_dict())

    if failures:
        print(f"{failures} page(s) failed; re-run to retry them.", file=sys.stderr)

    return sorted(specs.values(), key=lambda s: (s["category"], s["provider"].lower(), s["name"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="apps/catalog/data/catalog.json")
    parser.add_argument(
        "--cache-dir",
        default=".cache/kie-docs",
        help="Where to keep downloaded pages so re-runs skip the network (empty to disable).",
    )
    args = parser.parse_args()

    models = build(Path(args.cache_dir) if args.cache_dir else None)
    if not models:
        print("No models parsed; leaving the existing snapshot untouched.", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(models, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {out} with {len(models)} models.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for parsing the KIE documentation into a usable catalog."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from apps.catalog.services import catalog
from apps.catalog.services.docs import (
    ModelRef,
    ModelSpec,
    is_utility,
    parse_index,
    parse_specs,
    poll_path_for,
    provider_for_section,
)

INDEX_SAMPLE = """\
# docs.kie.ai

## Docs
- [Getting Started with KIE API (Important)](https://docs.kie.ai/1973359m0.md):
- Image    Models > Google [Google - Nano Banana](https://docs.kie.ai/market/google/nano-banana.md): Fast image generation
- Image    Models > Google [4o Image Generation Callbacks](https://docs.kie.ai/4o-image-api/generate-4-o-image-callbacks.md):
- Video Models > Kling [Kling V2.1 Pro](https://docs.kie.ai/market/kling/v2-1-pro.md): Video from an image
- Chat  Models > Claude [Claude Opus 5](https://docs.kie.ai/market/claude/claude-opus-5.md):
- Music Models > ElevenLabs [elevenlabs/text-to-speech-turbo-2-5](https://docs.kie.ai/market/elevenlabs/text-to-speech-turbo-2-5.md):
- Video Models > HappyHorse [HappyHorse 1.1 文生视频](https://docs.kie.ai/38309290e0.md):
- Image    Models > Google [Google - Nano Banana](https://docs.kie.ai/cnmarket/google/nano-banana.md):
"""

JOBS_SPEC = """\
# Google - Nano Banana

```yaml
openapi: 3.0.1
paths:
  /api/v1/jobs/createTask:
    post:
      summary: Google - Nano Banana
      description: Content generation using google/nano-banana
      tags:
        - docs/en/Market/Image    Models/Google
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required:
                - model
                - input
              properties:
                model:
                  type: string
                  enum:
                    - google/nano-banana
                  default: google/nano-banana
                callBackUrl:
                  type: string
                input:
                  type: object
                  required:
                    - prompt
                  properties:
                    prompt:
                      type: string
                      maxLength: 5000
                      description: The prompt for image generation
                    image_urls:
                      type: array
                      items:
                        type: string
                        format: uri
                      maxItems: 5
                    output_format:
                      type: string
                      enum:
                        - png
                        - jpeg
                      default: png
                    guidance:
                      type: number
                      minimum: 0
                      maximum: 10
                      default: 3.5
```
"""

CHAT_SPEC = """\
# Codex

```yaml
openapi: 3.0.1
paths:
  /api/v1/responses:
    post:
      summary: GPT Codex
      tags:
        - docs/en/Market/Chat  Models/Codex
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required:
                - model
                - input
              properties:
                model:
                  type: string
                  enum:
                    - gpt-5-codex
                    - gpt-5.1-codex
                stream:
                  type: boolean
```
"""

GROK_SPEC = """\
# Grok

```yaml
openapi: 3.0.1
paths:
  /grok/v1/responses:
    post:
      summary: Grok 4.6
      tags:
        - docs/en/Market/Chat  Models/Grok
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                model:
                  type: string
                  description: 'Target model name. Allowed values: `grok-4-6`.'
            example:
              model: grok-4-6
              input: []
```
"""


class ParseIndexTests(TestCase):
    def test_groups_models_by_category(self):
        refs = parse_index(INDEX_SAMPLE)
        by_category: dict[str, list[str]] = {}
        for ref in refs:
            by_category.setdefault(ref.category, []).append(ref.name)

        self.assertEqual(by_category["image"], ["Google - Nano Banana"])
        self.assertEqual(by_category["video"], ["Kling V2.1 Pro"])
        self.assertEqual(by_category["chat"], ["Claude Opus 5"])
        self.assertEqual(by_category["music"], ["elevenlabs/text-to-speech-turbo-2-5"])

    def test_skips_pages_that_are_not_models(self):
        names = {ref.name for ref in parse_index(INDEX_SAMPLE)}
        self.assertNotIn("4o Image Generation Callbacks", names)
        self.assertNotIn("Getting Started with KIE API (Important)", names)

    def test_skips_localized_mirrors_and_permalinks(self):
        urls = {ref.doc_url for ref in parse_index(INDEX_SAMPLE)}
        self.assertNotIn("https://docs.kie.ai/cnmarket/google/nano-banana.md", urls)
        self.assertNotIn("https://docs.kie.ai/38309290e0.md", urls)

    def test_slug_comes_from_the_documentation_path(self):
        ref = next(r for r in parse_index(INDEX_SAMPLE) if r.name == "Kling V2.1 Pro")
        self.assertEqual(ref.slug, "market-kling-v2-1-pro")


class ParseSpecTests(TestCase):
    def setUp(self):
        self.image_ref = ModelRef(
            slug="market-google-nano-banana",
            name="Google - Nano Banana",
            category="image",
            provider="Google",
            doc_url="https://docs.kie.ai/market/google/nano-banana.md",
        )

    def test_reads_a_jobs_model(self):
        spec = parse_specs(JOBS_SPEC, self.image_ref)[0]

        self.assertEqual(spec.model_id, "google/nano-banana")
        self.assertEqual(spec.adapter, "jobs")
        self.assertEqual(spec.create_path, "/api/v1/jobs/createTask")
        self.assertEqual(spec.prompt_field, "prompt")
        self.assertEqual(spec.image_field, "image_urls")

    def test_reads_field_constraints(self):
        spec = parse_specs(JOBS_SPEC, self.image_ref)[0]
        fields = {field["name"]: field for field in spec.fields}

        self.assertTrue(fields["prompt"]["required"])
        self.assertFalse(fields["output_format"]["required"])
        self.assertEqual(fields["output_format"]["enum"], ["png", "jpeg"])
        self.assertEqual(fields["guidance"]["minimum"], 0)
        self.assertEqual(fields["guidance"]["maximum"], 10)
        self.assertEqual(fields["image_urls"]["max_items"], 5)
        # callBackUrl is ours to set, not the user's.
        self.assertNotIn("callBackUrl", fields)

    def test_one_page_can_document_several_chat_models(self):
        ref = ModelRef(
            slug="market-codex-gpt-codex",
            name="GPT Codex",
            category="chat",
            provider="Codex",
            doc_url="https://docs.kie.ai/market/codex/gpt-codex.md",
        )
        specs = parse_specs(CHAT_SPEC, ref)

        self.assertEqual([spec.model_id for spec in specs], ["gpt-5-codex", "gpt-5.1-codex"])
        self.assertEqual({spec.adapter for spec in specs}, {"chat_responses"})
        # Slugs have to stay distinct or the second model shadows the first.
        self.assertEqual(len({spec.slug for spec in specs}), 2)

    def test_recovers_a_model_id_that_is_only_in_the_example(self):
        ref = ModelRef(
            slug="market-grok-grok-4-6",
            name="Grok 4.6",
            category="chat",
            provider="Grok",
            doc_url="https://docs.kie.ai/market/grok/grok-4-6.md",
        )
        spec = parse_specs(GROK_SPEC, ref)[0]
        self.assertEqual(spec.model_id, "grok-4-6")


TASK_SPEC = """\
# Generate Aleph Video

```yaml
openapi: 3.0.1
paths:
  /api/v1/aleph/generate:
    post:
      summary: Generate Aleph Video
      tags:
        - docs/en/Market/Video Models/Runway API/Aleph
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required:
                - prompt
              properties:
                prompt:
                  type: string
                videoUrl:
                  type: string
                callBackUrl:
                  type: string
```
"""


class TaskEndpointTests(TestCase):
    """KIE's product APIs predate the unified jobs endpoint but share its shape."""

    def test_reads_a_dedicated_endpoint_as_a_task_model(self):
        ref = ModelRef(
            slug="runway-api-generate-aleph-video",
            name="Generate Aleph Video",
            category="video",
            provider="Runway",
            doc_url="https://docs.kie.ai/runway-api/generate-aleph-video.md",
        )
        spec = parse_specs(TASK_SPEC, ref)[0]

        self.assertEqual(spec.adapter, "task")
        self.assertEqual(spec.create_path, "/api/v1/aleph/generate")
        self.assertEqual(spec.poll_path, "/api/v1/aleph/record-info")
        self.assertEqual(spec.category, "video")
        self.assertEqual([f["name"] for f in spec.fields], ["prompt", "videoUrl"])
        self.assertTrue(spec.fields[0]["required"])

    def test_poll_routes_follow_the_product_family(self):
        cases = {
            "/api/v1/generate": "/api/v1/generate/record-info",
            "/api/v1/generate/add-vocals": "/api/v1/generate/record-info",
            "/api/v1/veo/generate": "/api/v1/veo/record-info",
            "/api/v1/veo/extend": "/api/v1/veo/record-info",
            "/api/v1/flux/kontext/generate": "/api/v1/flux/kontext/record-info",
            "/api/v1/suno/cover/generate": "/api/v1/suno/cover/record-info",
            "/api/v1/mp4/generate": "/api/v1/mp4/record-info",
            "/api/v1/lyrics": "/api/v1/lyrics/record-info",
            # Runway is the one family that names its route differently.
            "/api/v1/runway/generate": "/api/v1/runway/record-detail",
        }
        for create_path, expected in cases.items():
            with self.subTest(create_path=create_path):
                self.assertEqual(poll_path_for(create_path), expected)

    def test_utility_endpoints_are_not_models(self):
        for path in (
            "/api/file-stream-upload",
            "/api/v1/common/download-url",
            "/api/v1/gpt4o-image/download-url",
            "/api/v1/voice/check-voice",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_utility(path))

        self.assertFalse(is_utility("/api/v1/veo/generate"))


class DescriptionTests(TestCase):
    def test_strips_the_integration_boilerplate(self):
        from apps.catalog.services.docs import clean_text

        raw = (
            "Content generation using bytedance/v1-lite-image-to-video\n\n"
            "## Query Task Status\n\n"
            "After submitting a task, use the unified query endpoint.\n\n"
            "::: tip[]\n\n"
            "For production use, we recommend using the callBackUrl parameter.\n\n"
            ":::\n\n"
            "## Related Resources\n"
        )
        self.assertEqual(
            clean_text(raw), "Content generation using bytedance/v1-lite-image-to-video"
        )

    def test_leaves_a_plain_description_alone(self):
        from apps.catalog.services.docs import clean_text

        self.assertEqual(clean_text("High-quality image editing."), "High-quality image editing.")


class ProviderNamingTests(TestCase):
    def test_model_sections_name_the_vendor_after_the_category(self):
        self.assertEqual(provider_for_section("Image    Models > Google"), "Google")
        self.assertEqual(provider_for_section("Video Models > Runway API > Aleph"), "Runway")
        self.assertEqual(provider_for_section("Image    Models > 4o Image API"), "4o Image")

    def test_product_sections_name_the_product_first(self):
        # "Suno API > Music Generation" is one product's operations, not a
        # provider called "Music Generation".
        self.assertEqual(provider_for_section("Suno API > Music Generation"), "Suno")
        self.assertEqual(provider_for_section("Suno API > Vocal Removal"), "Suno")
        self.assertEqual(provider_for_section("Veo3.1 API"), "Veo3.1")

    def test_tags_and_index_sections_agree(self):
        from apps.catalog.services.docs import classify_tags

        self.assertEqual(
            classify_tags(["docs/en/Market/Video Models/Runway API/Aleph"]), ("video", "Runway")
        )
        self.assertEqual(classify_tags(["docs/en/Suno API/Music Generation"]), ("music", "Suno"))


class ResolveSpecTests(TestCase):
    """Which model a category page greets you with."""

    def setUp(self):
        cache.clear()

    def _refs(self):
        return [
            ModelRef(slug="a-utility", name="Audio isolation", category="music",
                     provider="ElevenLabs", doc_url=""),
            ModelRef(slug="b-generator", name="Generate music", category="music",
                     provider="Suno", doc_url=""),
        ]

    def _specs(self):
        return {
            "a-utility": ModelSpec(slug="a-utility", model_id="x", name="Audio isolation",
                                   provider="ElevenLabs", category="music", adapter="jobs",
                                   create_path="/p", prompt_field=""),
            "b-generator": ModelSpec(slug="b-generator", model_id="y", name="Generate music",
                                     provider="Suno", category="music", adapter="jobs",
                                     create_path="/p", prompt_field="prompt"),
        }

    def test_prefers_a_model_you_can_prompt(self):
        specs = self._specs()
        with (
            mock.patch.object(catalog, "index", return_value=self._refs()),
            mock.patch.object(catalog, "spec", side_effect=lambda slug, **kw: specs.get(slug)),
        ):
            found, slug = catalog.resolve_spec("music")

        self.assertEqual(slug, "b-generator")
        self.assertEqual(found.name, "Generate music")

    def test_an_explicit_choice_wins(self):
        specs = self._specs()
        with (
            mock.patch.object(catalog, "index", return_value=self._refs()),
            mock.patch.object(catalog, "spec", side_effect=lambda slug, **kw: specs.get(slug)),
        ):
            found, slug = catalog.resolve_spec("music", "a-utility")

        self.assertEqual(slug, "a-utility")

    def test_falls_back_past_a_model_with_no_usable_schema(self):
        specs = self._specs()
        specs["b-generator"] = None
        with (
            mock.patch.object(catalog, "index", return_value=self._refs()),
            mock.patch.object(catalog, "spec", side_effect=lambda slug, **kw: specs.get(slug)),
        ):
            found, slug = catalog.resolve_spec("music")

        self.assertEqual(slug, "a-utility")

    def test_an_empty_category_resolves_to_nothing(self):
        with mock.patch.object(catalog, "index", return_value=[]):
            self.assertEqual(catalog.resolve_spec("music"), (None, ""))


class SnapshotTests(TestCase):
    """The bundled snapshot is the offline fallback, so it has to stay valid."""

    def setUp(self):
        cache.clear()

    def test_snapshot_covers_every_category(self):
        path = Path(catalog.SNAPSHOT_PATH)
        rows = json.loads(path.read_text())
        categories = {row["category"] for row in rows}
        self.assertEqual(categories, {"chat", "image", "video", "music"})

    def test_snapshot_rows_are_complete(self):
        rows = json.loads(Path(catalog.SNAPSHOT_PATH).read_text())
        for row in rows:
            self.assertTrue(row["slug"], row)
            self.assertTrue(row["model_id"], row)
            self.assertTrue(row["adapter"], row)
            self.assertTrue(row["create_path"], row)

    def test_slugs_are_unique(self):
        rows = json.loads(Path(catalog.SNAPSHOT_PATH).read_text())
        slugs = [row["slug"] for row in rows]
        self.assertEqual(len(slugs), len(set(slugs)))

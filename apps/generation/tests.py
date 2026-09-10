"""Tests for building generation requests and reading their results."""

from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase

from apps.catalog.services.docs import ModelSpec
from apps.generation.models import Generation
from apps.generation.services import engine, inputs, results

User = get_user_model()


def image_spec(**overrides) -> ModelSpec:
    defaults = {
        "slug": "market-google-nano-banana",
        "model_id": "google/nano-banana",
        "name": "Nano Banana",
        "provider": "Google",
        "category": "image",
        "adapter": "jobs",
        "create_path": "/api/v1/jobs/createTask",
        "poll_path": "/api/v1/jobs/recordInfo",
        "prompt_field": "prompt",
        "image_field": "image_urls",
        "fields": [
            {"name": "prompt", "type": "string", "label": "Prompt", "required": True},
            {"name": "image_urls", "type": "array", "max_items": 3, "required": False},
            {"name": "output_format", "type": "string", "enum": ["png", "jpeg"], "required": False},
            {"name": "num_images", "type": "integer", "required": False},
            {"name": "guidance", "type": "number", "required": False},
            {"name": "watermark", "type": "boolean", "default": False, "required": False},
        ],
    }
    defaults.update(overrides)
    return ModelSpec(**defaults)


def post(data: dict) -> QueryDict:
    query = QueryDict(mutable=True)
    for key, value in data.items():
        query[key] = value
    return query


class BuildInputTests(TestCase):
    def test_coerces_declared_types(self):
        model_input, prompt = inputs.build_input(
            image_spec(),
            post({"prompt": "a cat", "num_images": "2", "guidance": "3.5", "output_format": "png"}),
        )

        self.assertEqual(prompt, "a cat")
        self.assertEqual(model_input["num_images"], 2)
        self.assertIsInstance(model_input["num_images"], int)
        self.assertEqual(model_input["guidance"], 3.5)
        self.assertEqual(model_input["output_format"], "png")

    def test_blank_fields_are_left_out_so_model_defaults_apply(self):
        model_input, _ = inputs.build_input(
            image_spec(), post({"prompt": "a cat", "num_images": "", "output_format": ""})
        )
        self.assertEqual(set(model_input), {"prompt"})

    def test_ignores_fields_the_model_does_not_declare(self):
        model_input, _ = inputs.build_input(
            image_spec(), post({"prompt": "a cat", "callBackUrl": "https://evil.example", "admin": "1"})
        )
        self.assertNotIn("callBackUrl", model_input)
        self.assertNotIn("admin", model_input)

    def test_unchecked_boxes_stay_absent(self):
        checked, _ = inputs.build_input(image_spec(), post({"prompt": "x", "watermark": "on"}))
        unchecked, _ = inputs.build_input(image_spec(), post({"prompt": "x"}))

        self.assertIs(checked["watermark"], True)
        self.assertNotIn("watermark", unchecked)

    def test_uploaded_images_go_to_the_declared_field(self):
        model_input, _ = inputs.build_input(
            image_spec(), post({"prompt": "x"}), ["https://kie.example/a.png"]
        )
        self.assertEqual(model_input["image_urls"], ["https://kie.example/a.png"])

    def test_image_lists_respect_the_declared_maximum(self):
        urls = [f"https://kie.example/{i}.png" for i in range(6)]
        model_input, _ = inputs.build_input(image_spec(), post({"prompt": "x"}), urls)
        self.assertEqual(len(model_input["image_urls"]), 3)

    def test_a_single_image_field_takes_only_the_first_upload(self):
        spec = image_spec(
            image_field="image_url",
            fields=[{"name": "image_url", "type": "string", "required": False}],
        )
        model_input, _ = inputs.build_input(spec, post({}), ["https://a.png", "https://b.png"])
        self.assertEqual(model_input["image_url"], "https://a.png")

    def test_arrays_accept_one_value_per_line(self):
        spec = image_spec(fields=[{"name": "tags", "type": "array", "required": False}])
        model_input, _ = inputs.build_input(spec, post({"tags": "one\ntwo\n\nthree"}))
        self.assertEqual(model_input["tags"], ["one", "two", "three"])

    def test_missing_required_fields_are_reported(self):
        model_input, _ = inputs.build_input(image_spec(), post({"output_format": "png"}))
        self.assertEqual(inputs.missing_required(image_spec(), model_input), ["Prompt"])


class ResultParsingTests(TestCase):
    def test_reads_the_unified_jobs_payload(self):
        payload = {
            "taskId": "t1",
            "state": "success",
            "resultJson": '{"resultUrls": ["https://cdn.example/a.png"]}',
        }
        outcome = results.parse(payload, category="image")

        self.assertEqual(outcome.status, results.SUCCEEDED)
        self.assertEqual([media.url for media in outcome.media], ["https://cdn.example/a.png"])
        self.assertEqual(outcome.media[0].kind, "image")

    def test_detects_media_type_from_the_extension(self):
        payload = {"state": "success", "resultJson": {"resultUrls": ["https://cdn.example/clip.mp4"]}}
        outcome = results.parse(payload, category="video")
        self.assertEqual(outcome.media[0].kind, "video")

    def test_reads_suno_tracks_with_their_covers(self):
        payload = {
            "status": "SUCCESS",
            "data": {
                "data": [
                    {
                        "id": "s1",
                        "title": "Night drive",
                        "audioUrl": "https://cdn.example/track.mp3",
                        "imageUrl": "https://cdn.example/cover.jpg",
                        "duration": 128,
                    }
                ]
            },
        }
        outcome = results.parse(payload, category="music")

        self.assertEqual(outcome.status, results.SUCCEEDED)
        self.assertEqual(len(outcome.media), 1)
        self.assertEqual(outcome.media[0].kind, "audio")
        self.assertEqual(outcome.media[0].title, "Night drive")
        self.assertEqual(outcome.media[0].thumbnail_url, "https://cdn.example/cover.jpg")

    def test_running_tasks_are_not_finished(self):
        outcome = results.parse({"state": "waiting"}, category="image")
        self.assertEqual(outcome.status, results.PENDING)
        self.assertFalse(outcome.is_finished)

    def test_failure_carries_the_reason(self):
        outcome = results.parse({"state": "fail", "failMsg": "Prompt rejected"}, category="image")
        self.assertEqual(outcome.status, results.FAILED)
        self.assertEqual(outcome.error, "Prompt rejected")

    def test_success_without_files_counts_as_a_failure(self):
        outcome = results.parse({"state": "success", "resultJson": "{}"}, category="image")
        self.assertEqual(outcome.status, results.FAILED)
        self.assertIn("no files", outcome.error)

    def test_duplicate_urls_are_collapsed(self):
        payload = {
            "state": "success",
            "resultJson": {
                "resultUrls": ["https://cdn.example/a.png", "https://cdn.example/a.png"],
                "imageUrl": "https://cdn.example/a.png",
            },
        }
        self.assertEqual(len(results.parse(payload).media), 1)


class GenerationFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("bob", password="pw-for-testing-1")
        self.user.settings.kie_api_key = "test-key"
        self.user.settings.save()

    def test_start_records_the_task(self):
        client = mock.Mock()
        client.create_job.return_value = "task-123"

        with mock.patch("apps.generation.services.engine.kie.client_for", return_value=client):
            generation = engine.start(
                user=self.user, spec=image_spec(), form_data=post({"prompt": "a cat"})
            )

        self.assertEqual(generation.task_id, "task-123")
        self.assertEqual(generation.status, Generation.Status.RUNNING)
        self.assertEqual(generation.prompt, "a cat")
        client.create_job.assert_called_once_with("google/nano-banana", {"prompt": "a cat"})

    def test_a_rejected_request_is_stored_as_failed(self):
        from apps.common.services import kie

        client = mock.Mock()
        client.create_job.side_effect = kie.KieError("Insufficient credits")

        with mock.patch("apps.generation.services.engine.kie.client_for", return_value=client):
            generation = engine.start(
                user=self.user, spec=image_spec(), form_data=post({"prompt": "a cat"})
            )

        self.assertEqual(generation.status, Generation.Status.FAILED)
        self.assertEqual(generation.error, "Insufficient credits")

    def test_refresh_stores_assets_on_success(self):
        generation = Generation.objects.create(
            user=self.user,
            category="image",
            model_slug="market-google-nano-banana",
            adapter="jobs",
            task_id="task-123",
            status=Generation.Status.RUNNING,
        )

        client = mock.Mock()
        client.job_status.return_value = {
            "state": "success",
            "resultJson": {"resultUrls": ["https://cdn.example/a.png"]},
        }

        with (
            mock.patch("apps.generation.services.engine.kie.client_for", return_value=client),
            # The download is a network call; the asset must survive it failing.
            mock.patch("apps.generation.services.engine.download", return_value=False),
        ):
            generation = engine.refresh(generation)

        self.assertEqual(generation.status, Generation.Status.SUCCEEDED)
        self.assertEqual(generation.assets.count(), 1)
        self.assertEqual(generation.assets.first().remote_url, "https://cdn.example/a.png")

    def test_a_transient_polling_error_leaves_the_task_running(self):
        from apps.common.services import kie

        generation = Generation.objects.create(
            user=self.user,
            category="image",
            model_slug="x",
            adapter="jobs",
            task_id="task-123",
            status=Generation.Status.RUNNING,
        )

        client = mock.Mock()
        client.job_status.side_effect = kie.KieError("Gateway timeout")

        with mock.patch("apps.generation.services.engine.kie.client_for", return_value=client):
            generation = engine.refresh(generation)

        self.assertEqual(generation.status, Generation.Status.RUNNING)

    def test_finished_generations_are_not_polled_again(self):
        generation = Generation.objects.create(
            user=self.user,
            category="image",
            model_slug="x",
            task_id="t",
            status=Generation.Status.SUCCEEDED,
        )

        with mock.patch("apps.generation.services.engine.kie.client_for") as client_for:
            engine.refresh(generation)

        client_for.assert_not_called()

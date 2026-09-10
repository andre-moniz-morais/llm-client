"""End-to-end tests for the pages: auth, settings and the fragment endpoints."""

from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import UserSettings
from apps.catalog.services.docs import ModelRef, ModelSpec
from apps.chat.models import Conversation
from apps.generation.models import Generation

User = get_user_model()

CHAT_REF = ModelRef(
    slug="market-claude-claude-opus-5",
    name="Claude Opus 5",
    category="chat",
    provider="Claude",
    doc_url="https://docs.kie.ai/market/claude/claude-opus-5.md",
)
IMAGE_REF = ModelRef(
    slug="market-google-nano-banana",
    name="Google - Nano Banana",
    category="image",
    provider="Google",
    doc_url="https://docs.kie.ai/market/google/nano-banana.md",
)
IMAGE_SPEC = ModelSpec(
    slug=IMAGE_REF.slug,
    model_id="google/nano-banana",
    name=IMAGE_REF.name,
    provider="Google",
    category="image",
    adapter="jobs",
    create_path="/api/v1/jobs/createTask",
    prompt_field="prompt",
    image_field="image_urls",
    fields=[
        {
            "name": "prompt",
            "type": "string",
            "label": "Prompt",
            "description": "What to draw",
            "required": True,
            "enum": [],
        },
        {
            "name": "output_format",
            "type": "string",
            "label": "Output Format",
            "enum": ["png", "jpeg"],
            "default": "png",
            "required": False,
        },
    ],
)


class AuthPageTests(TestCase):
    def test_signed_out_visitors_are_sent_to_login(self):
        response = self.client.get(reverse("chat:index"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={reverse('chat:index')}")

    def test_registration_creates_a_user_with_settings(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "newcomer",
                "email": "new@example.com",
                "password1": "a-strong-passphrase-42",
                "password2": "a-strong-passphrase-42",
            },
        )

        self.assertRedirects(response, reverse("accounts:settings"))
        user = User.objects.get(username="newcomer")
        self.assertTrue(UserSettings.objects.filter(user=user).exists())

    def test_login_works(self):
        User.objects.create_user("someone", password="a-strong-passphrase-42")
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "someone", "password": "a-strong-passphrase-42"},
        )
        self.assertEqual(response.status_code, 302)


class SettingsPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("carol", password="a-strong-passphrase-42")
        self.client.force_login(self.user)

    def test_saving_a_key_stores_it_encrypted(self):
        self.client.post(
            reverse("accounts:settings"),
            {"section": "credentials", "kie_api_key": "sk-secret-value"},
        )

        settings_row = UserSettings.objects.get(user=self.user)
        self.assertEqual(settings_row.kie_api_key, "sk-secret-value")
        self.assertNotIn("sk-secret-value", settings_row.kie_api_key_encrypted)

    def test_the_key_is_never_rendered_back(self):
        self.user.settings.kie_api_key = "sk-secret-value"
        self.user.settings.save()

        response = self.client.get(reverse("accounts:settings"))
        self.assertNotContains(response, "sk-secret-value")
        self.assertContains(response, "Connected")

    def test_an_empty_submission_leaves_the_key_alone(self):
        self.user.settings.kie_api_key = "sk-secret-value"
        self.user.settings.save()

        self.client.post(reverse("accounts:settings"), {"section": "credentials", "kie_api_key": ""})

        self.user.settings.refresh_from_db()
        self.assertEqual(self.user.settings.kie_api_key, "sk-secret-value")

    def test_removal_is_explicit(self):
        self.user.settings.kie_api_key = "sk-secret-value"
        self.user.settings.save()

        self.client.post(
            reverse("accounts:settings"),
            {"section": "credentials", "remove_kie_api_key": "on"},
        )

        self.user.settings.refresh_from_db()
        self.assertEqual(self.user.settings.kie_api_key, "")


class BrowserNotificationSettingTests(TestCase):
    """The preference the chat page reads before raising a desktop notification."""

    def setUp(self):
        self.user = User.objects.create_user("erin", password="a-strong-passphrase-42")
        self.client.force_login(self.user)

    def test_it_is_off_until_asked_for(self):
        self.assertFalse(self.user.settings.browser_notifications_enabled)

    def test_enabling_it_is_stored(self):
        self.client.post(
            reverse("accounts:settings"),
            {"section": "browser", "browser_notifications_enabled": "on"},
        )

        self.user.settings.refresh_from_db()
        self.assertTrue(self.user.settings.browser_notifications_enabled)

    def test_an_unchecked_box_turns_it_off(self):
        self.user.settings.browser_notifications_enabled = True
        self.user.settings.save()

        self.client.post(reverse("accounts:settings"), {"section": "browser"})

        self.user.settings.refresh_from_db()
        self.assertFalse(self.user.settings.browser_notifications_enabled)

    def test_saving_notifications_leaves_the_telegram_settings_alone(self):
        """Each panel posts its own section, so one must not clear another."""
        self.user.settings.telegram_chat_id = "12345"
        self.user.settings.telegram_notifications_enabled = True
        self.user.settings.save()

        self.client.post(
            reverse("accounts:settings"),
            {"section": "browser", "browser_notifications_enabled": "on"},
        )

        self.user.settings.refresh_from_db()
        self.assertEqual(self.user.settings.telegram_chat_id, "12345")
        self.assertTrue(self.user.settings.telegram_notifications_enabled)


class HealthCheckTests(TestCase):
    def test_it_answers_without_authentication(self):
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")


class PageRenderTests(TestCase):
    """The pages load their model lists on request, so the catalog is stubbed."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("dave", password="a-strong-passphrase-42")
        self.user.settings.kie_api_key = "test-key"
        self.user.settings.save()
        self.client.force_login(self.user)

    def test_chat_page_lists_chat_models(self):
        with mock.patch("apps.catalog.services.catalog.index", return_value=[CHAT_REF, IMAGE_REF]):
            response = self.client.get(reverse("chat:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Claude Opus 5")
        # A model from another category must not appear in the chat picker.
        self.assertNotContains(response, "Google - Nano Banana")

    def test_chat_page_carries_the_notification_preference(self):
        """chat.js reads this attribute to decide whether to raise a notification."""
        with mock.patch("apps.catalog.services.catalog.index", return_value=[CHAT_REF]):
            response = self.client.get(reverse("chat:index"))
        self.assertContains(response, 'data-notify="false"')

        self.user.settings.browser_notifications_enabled = True
        self.user.settings.save()

        with mock.patch("apps.catalog.services.catalog.index", return_value=[CHAT_REF]):
            response = self.client.get(reverse("chat:index"))
        self.assertContains(response, 'data-notify="true"')

    def test_studio_page_renders_the_selected_model_form(self):
        with (
            mock.patch("apps.catalog.services.catalog.index", return_value=[CHAT_REF, IMAGE_REF]),
            mock.patch("apps.catalog.services.catalog.spec", return_value=IMAGE_SPEC),
        ):
            response = self.client.get(reverse("generation:image"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Google - Nano Banana")
        self.assertContains(response, 'name="prompt"')
        self.assertContains(response, 'name="output_format"')
        self.assertContains(response, "reference_images")

    def test_unknown_category_is_404(self):
        response = self.client.get("/studio/nonsense/")
        self.assertEqual(response.status_code, 404)

    def test_model_form_fragment_is_html(self):
        with mock.patch("apps.catalog.services.catalog.spec", return_value=IMAGE_SPEC):
            response = self.client.get(
                reverse("generation:model-form", args=[IMAGE_SPEC.slug])
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        self.assertContains(response, 'name="prompt"')

    def test_model_form_for_an_unknown_model_returns_a_styled_error(self):
        with mock.patch("apps.catalog.services.catalog.spec", return_value=None):
            response = self.client.get(reverse("generation:model-form", args=["nope"]))

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "callout", status_code=404)

    def test_settings_page_renders(self):
        self.assertEqual(self.client.get(reverse("accounts:settings")).status_code, 200)

    def test_library_page_renders(self):
        Generation.objects.create(
            user=self.user,
            category="image",
            model_slug="x",
            status=Generation.Status.SUCCEEDED,
            prompt="a cat",
        )
        response = self.client.get(reverse("generation:gallery"))
        self.assertEqual(response.status_code, 200)

    def test_pwa_manifest_and_service_worker_are_served(self):
        manifest = self.client.get(reverse("manifest"))
        worker = self.client.get(reverse("service-worker"))

        self.assertEqual(manifest.status_code, 200)
        self.assertIn("application/manifest+json", manifest["Content-Type"])
        self.assertContains(manifest, '"display": "standalone"')
        self.assertEqual(worker.status_code, 200)


class ChatFragmentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("erin", password="a-strong-passphrase-42")
        self.user.settings.kie_api_key = "test-key"
        self.user.settings.save()
        self.client.force_login(self.user)

    def test_sending_returns_rendered_messages_and_the_new_conversation_id(self):
        client = mock.Mock()
        # Shaped for the Anthropic Messages protocol the stubbed model speaks.
        client.chat.return_value = {"content": [{"type": "text", "text": "<p>Hello back</p>"}]}
        chat_spec = ModelSpec(
            slug=CHAT_REF.slug,
            model_id="claude-opus-5",
            name="Claude Opus 5",
            provider="Claude",
            category="chat",
            adapter="chat_anthropic",
            create_path="/claude/v1/messages",
        )

        with (
            mock.patch("apps.catalog.services.catalog.spec", return_value=chat_spec),
            mock.patch("apps.chat.services.engine.kie.client_for", return_value=client),
        ):
            response = self.client.post(
                reverse("chat:send"), {"message": "Hi there", "model": CHAT_REF.slug}
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hello back")
        self.assertContains(response, "Hi there")
        self.assertTrue(response["X-Conversation-Id"])
        self.assertEqual(Conversation.objects.filter(user=self.user).count(), 1)

    def test_headers_stay_encodable_for_a_non_ascii_message(self):
        client = mock.Mock()
        client.chat.return_value = {"content": [{"type": "text", "text": "<p>Ok</p>"}]}
        chat_spec = ModelSpec(
            slug=CHAT_REF.slug,
            model_id="claude-opus-5",
            name="Claude Opus 5",
            provider="Claude",
            category="chat",
            adapter="chat_anthropic",
            create_path="/claude/v1/messages",
        )

        with (
            mock.patch("apps.catalog.services.catalog.spec", return_value=chat_spec),
            mock.patch("apps.chat.services.engine.kie.client_for", return_value=client),
        ):
            response = self.client.post(
                reverse("chat:send"),
                {"message": "¿Qué es un haiku? 俳句", "model": CHAT_REF.slug},
            )

        self.assertEqual(response.status_code, 200)
        for name, value in response.items():
            with self.subTest(header=name):
                value.encode("latin-1")

    def test_an_empty_message_is_rejected(self):
        response = self.client.post(reverse("chat:send"), {"message": "  "})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Type a message", status_code=400)

    def test_a_conversation_belongs_to_its_owner(self):
        other = User.objects.create_user("mallory", password="a-strong-passphrase-42")
        conversation = Conversation.objects.create(user=other, model_slug="x")

        response = self.client.get(reverse("chat:detail", args=[conversation.pk]))
        self.assertEqual(response.status_code, 404)


class GenerationFragmentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("frank", password="a-strong-passphrase-42")
        self.user.settings.kie_api_key = "test-key"
        self.user.settings.save()
        self.client.force_login(self.user)

    def test_creating_returns_a_polling_card(self):
        client = mock.Mock()
        client.create_job.return_value = "task-abc"

        with (
            mock.patch("apps.catalog.services.catalog.spec", return_value=IMAGE_SPEC),
            mock.patch("apps.generation.services.engine.kie.client_for", return_value=client),
        ):
            response = self.client.post(
                reverse("generation:create", args=[IMAGE_SPEC.slug]), {"prompt": "a cat"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-poll")
        self.assertContains(response, "Working")

    def test_a_missing_required_field_is_reported_without_calling_kie(self):
        with (
            mock.patch("apps.catalog.services.catalog.spec", return_value=IMAGE_SPEC),
            mock.patch("apps.generation.services.engine.kie.client_for") as client_for,
        ):
            response = self.client.post(
                reverse("generation:create", args=[IMAGE_SPEC.slug]), {"prompt": ""}
            )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Prompt", status_code=400)
        client_for.assert_not_called()

    def test_status_reports_the_finished_state_in_a_header(self):
        generation = Generation.objects.create(
            user=self.user,
            category="image",
            model_slug=IMAGE_SPEC.slug,
            adapter="jobs",
            task_id="task-abc",
            status=Generation.Status.RUNNING,
        )

        client = mock.Mock()
        client.job_status.return_value = {
            "state": "success",
            "resultJson": {"resultUrls": ["https://cdn.example/a.png"]},
        }

        with (
            mock.patch("apps.generation.services.engine.kie.client_for", return_value=client),
            mock.patch("apps.generation.services.engine.download", return_value=False),
        ):
            response = self.client.get(reverse("generation:status", args=[generation.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-poll")
        # WSGI requires an exact str; a TextChoices member would 500 in a real
        # server even though the test client tolerates it.
        self.assertEqual(response["X-Generation-Status"], "succeeded")
        self.assertIs(type(response["X-Generation-Status"]), str)

    def test_another_users_generation_is_not_reachable(self):
        other = User.objects.create_user("mallory2", password="a-strong-passphrase-42")
        generation = Generation.objects.create(user=other, category="image", model_slug="x")

        response = self.client.get(reverse("generation:status", args=[generation.pk]))
        self.assertEqual(response.status_code, 404)


class ApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("grace", password="a-strong-passphrase-42")
        self.client.force_login(self.user)

    def test_models_endpoint_filters_by_category(self):
        with mock.patch("apps.catalog.services.catalog.index", return_value=[CHAT_REF, IMAGE_REF]):
            response = self.client.get("/api/models/?category=image")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["slug"] for row in response.json()], [IMAGE_REF.slug])

    def test_models_endpoint_searches(self):
        with mock.patch("apps.catalog.services.catalog.index", return_value=[CHAT_REF, IMAGE_REF]):
            response = self.client.get("/api/models/?search=banana")

        self.assertEqual([row["slug"] for row in response.json()], [IMAGE_REF.slug])

    def test_settings_endpoint_never_returns_the_key(self):
        self.user.settings.kie_api_key = "sk-secret-value"
        self.user.settings.save()

        response = self.client.get("/api/settings/me/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sk-secret-value", response.content.decode())
        self.assertTrue(response.json()["has_kie_key"])

    def test_generations_are_scoped_to_the_user(self):
        other = User.objects.create_user("mallory3", password="a-strong-passphrase-42")
        Generation.objects.create(user=other, category="image", model_slug="x")
        Generation.objects.create(user=self.user, category="image", model_slug="y")

        response = self.client.get("/api/generations/")
        self.assertEqual(response.json()["count"], 1)

    def test_the_api_requires_authentication(self):
        self.client.logout()
        self.assertEqual(self.client.get("/api/generations/").status_code, 403)

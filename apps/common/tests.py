"""Tests for the HTML safety layer, the credential store and deployment config.

Model output is injected into the page verbatim, so the sanitiser is the
boundary that matters most here.
"""

from __future__ import annotations

import os
from unittest import mock

from django.test import TestCase, override_settings

from apps.common.services import crypto
from apps.common.services import html as html_service
from config.settings import _database_config


class SanitizeTests(TestCase):
    def test_keeps_the_markup_the_prompt_asks_for(self):
        source = (
            '<h3>Heading</h3><p>Text with <strong>bold</strong> and <code>code</code>.</p>'
            '<div class="callout callout-warn">Careful</div>'
            '<ul><li>One</li></ul>'
            '<pre><code class="language-python">print(1)</code></pre>'
            '<table><tr><th scope="col">A</th><td colspan="2">B</td></tr></table>'
        )
        cleaned = html_service.sanitize(source)

        self.assertIn("<h3>Heading</h3>", cleaned)
        self.assertIn('class="callout callout-warn"', cleaned)
        self.assertIn('class="language-python"', cleaned)
        self.assertIn('colspan="2"', cleaned)

    def test_strips_scripts(self):
        cleaned = html_service.sanitize('<p>Hi</p><script>alert(1)</script>')
        self.assertNotIn("script", cleaned.lower())
        self.assertIn("<p>Hi</p>", cleaned)

    def test_strips_event_handlers(self):
        cleaned = html_service.sanitize('<p onclick="steal()">Hi</p><img src="x" onerror="steal()">')
        self.assertNotIn("onclick", cleaned)
        self.assertNotIn("onerror", cleaned)

    def test_strips_javascript_urls(self):
        cleaned = html_service.sanitize('<a href="javascript:alert(1)">go</a>')
        self.assertNotIn("javascript:", cleaned)

    def test_strips_embedding_and_styling_tags(self):
        cleaned = html_service.sanitize(
            '<iframe src="https://evil.example"></iframe>'
            '<style>body{display:none}</style>'
            '<form action="/x"><input name="password"></form>'
        )
        for tag in ("iframe", "style", "<form", "<input"):
            self.assertNotIn(tag, cleaned.lower())

    def test_drops_inline_styles(self):
        cleaned = html_service.sanitize('<p style="position:fixed;inset:0">covering</p>')
        self.assertNotIn("style=", cleaned)

    def test_external_links_get_safe_rel(self):
        cleaned = html_service.sanitize('<a href="https://example.com">x</a>')
        self.assertIn("noopener", cleaned)


class NormalizeReplyTests(TestCase):
    def test_plain_text_becomes_paragraphs(self):
        cleaned = html_service.normalize_reply("First line.\n\nSecond line.")
        self.assertEqual(cleaned, "<p>First line.</p><p>Second line.</p>")

    def test_plain_text_is_escaped(self):
        cleaned = html_service.normalize_reply("Use <script> carefully & well")
        self.assertNotIn("<script>", cleaned)
        self.assertIn("&lt;script&gt;", cleaned)

    def test_unwraps_a_fenced_answer(self):
        cleaned = html_service.normalize_reply("```html\n<p>Wrapped</p>\n```")
        self.assertEqual(cleaned, "<p>Wrapped</p>")

    def test_html_answers_pass_through_sanitised(self):
        cleaned = html_service.normalize_reply('<p>Ok</p><script>bad()</script>')
        self.assertEqual(cleaned, "<p>Ok</p>")

    def test_empty_input_is_empty(self):
        self.assertEqual(html_service.normalize_reply(""), "")


class HtmlToTextTests(TestCase):
    def test_flattens_for_telegram(self):
        text = html_service.html_to_text("<h3>Title</h3><p>One</p><ul><li>A</li><li>B</li></ul>")
        self.assertIn("Title", text)
        self.assertIn("• A", text)
        self.assertNotIn("<", text)

    def test_unescapes_entities(self):
        self.assertIn("a & b", html_service.html_to_text("<p>a &amp; b</p>"))


@override_settings(CREDENTIALS_ENCRYPTION_KEY="a-test-passphrase-for-encryption")
class CryptoTests(TestCase):
    def setUp(self):
        crypto._fernet.cache_clear()

    def tearDown(self):
        crypto._fernet.cache_clear()

    def test_round_trip(self):
        secret = "sk-live-abcdef123456"
        stored = crypto.encrypt(secret)

        self.assertNotIn(secret, stored)
        self.assertEqual(crypto.decrypt(stored), secret)

    def test_empty_stays_empty(self):
        self.assertEqual(crypto.encrypt(""), "")
        self.assertEqual(crypto.decrypt(""), "")

    def test_unreadable_ciphertext_returns_empty(self):
        self.assertEqual(crypto.decrypt("enc:v1:not-a-real-token"), "")

    def test_mask_hides_the_middle(self):
        masked = crypto.mask("sk-1234567890abcdef")
        self.assertTrue(masked.startswith("sk-1"))
        self.assertTrue(masked.endswith("cdef"))
        self.assertNotIn("567890", masked)


class DatabaseConfigTests(TestCase):
    """A checkout runs on SQLite; a deployment points at PostgreSQL."""

    def _config(self, **environment) -> dict:
        # clear=True so the developer's own DATABASE_URL cannot leak in.
        with mock.patch.dict(os.environ, environment, clear=True):
            return _database_config()

    def test_nothing_configured_means_sqlite(self):
        config = self._config()
        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")

    def test_a_database_url_selects_postgres(self):
        config = self._config(DATABASE_URL="postgresql://bob:secret@db.internal:6543/craftdb")
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["NAME"], "craftdb")
        self.assertEqual(config["USER"], "bob")
        self.assertEqual(config["PASSWORD"], "secret")
        self.assertEqual(config["HOST"], "db.internal")
        self.assertEqual(config["PORT"], "6543")

    def test_a_percent_encoded_password_is_decoded(self):
        """Passwords with reserved characters have to be encoded in a URL."""
        config = self._config(DATABASE_URL="postgres://u:p%40ss%2Fword@db:5432/craftdb")
        self.assertEqual(config["PASSWORD"], "p@ss/word")

    def test_an_unsupported_scheme_fails_loudly(self):
        with self.assertRaises(ValueError):
            self._config(DATABASE_URL="mysql://u:p@db:3306/craftdb")

    def test_discrete_variables_also_select_postgres(self):
        config = self._config(
            DJANGO_DB_HOST="postgres",
            DJANGO_DB_NAME="craftdb",
            DJANGO_DB_USER="craft",
            DJANGO_DB_PASSWORD="secret",
        )
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["HOST"], "postgres")
        self.assertEqual(config["PORT"], "5432")

    def test_a_database_url_wins_over_discrete_variables(self):
        config = self._config(
            DATABASE_URL="postgresql://a:b@from-url:5432/urldb",
            DJANGO_DB_HOST="from-discrete",
        )
        self.assertEqual(config["HOST"], "from-url")

    def test_postgres_keeps_connections_and_checks_them(self):
        """A pooled connection to a container that restarts must be validated."""
        config = self._config(DATABASE_URL="postgresql://a:b@db:5432/craftdb")
        self.assertTrue(config["CONN_HEALTH_CHECKS"])
        self.assertGreater(config["CONN_MAX_AGE"], 0)

"""Tests for the chat protocol adapters and the turn pipeline."""

from __future__ import annotations

import contextlib
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.services.docs import ModelSpec
from apps.chat.models import Conversation, Message
from apps.chat.services import engine, protocols
from apps.chat.services.protocols import Turn

User = get_user_model()


@contextlib.contextmanager
def patches(*managers):
    """Enter several patches as a single context manager."""
    with contextlib.ExitStack() as stack:
        for manager in managers:
            stack.enter_context(manager)
        yield


def spec_for(adapter: str, **overrides) -> ModelSpec:
    defaults = {
        "slug": "test-model",
        "model_id": "test-model-1",
        "name": "Test model",
        "provider": "Test",
        "category": "chat",
        "adapter": adapter,
        "create_path": "/test/v1/chat/completions",
    }
    defaults.update(overrides)
    return ModelSpec(**defaults)


TURNS = [
    Turn(role="user", text="Hello", image_urls=["https://example.com/a.png"]),
    Turn(role="assistant", text="Hi"),
    Turn(role="user", text="And now?"),
]


class BuildRequestTests(TestCase):
    def test_openai_shape(self):
        body = protocols.build_request(spec_for("chat_openai"), TURNS, "SYSTEM")

        self.assertEqual(body["messages"][0], {"role": "system", "content": "SYSTEM"})
        self.assertFalse(body["stream"])
        first_user = body["messages"][1]
        self.assertEqual(first_user["content"][0], {"type": "text", "text": "Hello"})
        self.assertEqual(
            first_user["content"][1],
            {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        )
        # A turn without images stays a plain string, as the docs show.
        self.assertEqual(body["messages"][3]["content"], "And now?")

    def test_openai_omits_model_when_the_path_selects_it(self):
        with_model = protocols.build_request(spec_for("chat_openai"), TURNS, "")
        self.assertEqual(with_model["model"], "test-model-1")

        path_only = spec_for("chat_openai", sends_model_in_body=False)
        self.assertNotIn("model", protocols.build_request(path_only, TURNS, ""))

    def test_anthropic_keeps_system_out_of_messages(self):
        body = protocols.build_request(spec_for("chat_anthropic"), TURNS, "SYSTEM")

        self.assertEqual(body["system"], "SYSTEM")
        self.assertEqual(body["max_tokens"], protocols.DEFAULT_MAX_TOKENS)
        self.assertNotIn("system", [message["role"] for message in body["messages"]])
        self.assertEqual(
            body["messages"][0]["content"][0],
            {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}},
        )

    def test_responses_uses_input_and_typed_blocks(self):
        body = protocols.build_request(spec_for("chat_responses"), TURNS, "SYSTEM")

        self.assertIn("input", body)
        self.assertNotIn("messages", body)
        self.assertEqual(body["input"][1]["content"][0]["type"], "input_text")
        self.assertEqual(body["input"][1]["content"][1]["type"], "input_image")
        # Assistant turns are prior output, not new input.
        self.assertEqual(body["input"][2]["content"][0]["type"], "output_text")

    def test_gemini_uses_contents_and_parts(self):
        body = protocols.build_request(spec_for("chat_gemini"), TURNS, "SYSTEM")

        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "SYSTEM"}]})
        self.assertEqual(body["contents"][0]["role"], "user")
        self.assertEqual(body["contents"][1]["role"], "model")
        self.assertIn("fileData", body["contents"][0]["parts"][1])

    def test_unknown_adapter_is_rejected(self):
        with self.assertRaises(protocols.UnsupportedProtocol):
            protocols.build_request(spec_for("jobs"), TURNS, "")


class ReadReplyTests(TestCase):
    def test_openai(self):
        payload = {"choices": [{"message": {"content": "<p>Answer</p>"}}]}
        self.assertEqual(
            protocols.read_reply(spec_for("chat_openai"), payload), "<p>Answer</p>"
        )

    def test_anthropic_joins_text_blocks(self):
        payload = {
            "content": [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "<p>A</p>"},
                {"type": "text", "text": "<p>B</p>"},
            ]
        }
        self.assertEqual(
            protocols.read_reply(spec_for("chat_anthropic"), payload), "<p>A</p>\n<p>B</p>"
        )

    def test_responses_prefers_the_flattened_field(self):
        payload = {"output_text": "Flat", "output": [{"content": [{"type": "output_text", "text": "Nested"}]}]}
        self.assertEqual(protocols.read_reply(spec_for("chat_responses"), payload), "Flat")

    def test_responses_falls_back_to_output_blocks(self):
        payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Nested"}]}]}
        self.assertEqual(protocols.read_reply(spec_for("chat_responses"), payload), "Nested")

    def test_gemini_ignores_reasoning_parts(self):
        payload = {
            "candidates": [
                {"content": {"parts": [{"text": "secret", "thought": True}, {"text": "Answer"}]}}
            ]
        }
        self.assertEqual(protocols.read_reply(spec_for("chat_gemini"), payload), "Answer")

    def test_unwraps_a_nested_payload(self):
        payload = {"data": {"choices": [{"message": {"content": "Deep"}}]}}
        self.assertEqual(protocols.read_reply(spec_for("chat_openai"), payload), "Deep")

    def test_usage_is_normalised(self):
        self.assertEqual(
            protocols.read_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 4}}),
            {"input_tokens": 10, "output_tokens": 4},
        )
        self.assertEqual(
            protocols.read_usage({"usageMetadata": {"promptTokenCount": 7, "totalTokenCount": 9}}),
            {"input_tokens": 7, "total_tokens": 9},
        )


class SendMessageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="pw-for-testing-1")
        self.user.settings.kie_api_key = "test-key"
        self.user.settings.save()
        self.conversation = Conversation.objects.create(
            user=self.user, model_slug="test-model", model_name="Test model"
        )

    def _kie(self, payload=None, error=None):
        """Stand in for the catalog lookup and the KIE call.

        Returns a context manager, so each test reads as "with this reply, ...".
        """
        client = mock.Mock()
        if error is not None:
            client.chat.side_effect = error
        else:
            client.chat.return_value = payload

        return patches(
            mock.patch(
                "apps.chat.services.engine.catalog.spec",
                return_value=spec_for("chat_openai"),
            ),
            mock.patch("apps.chat.services.engine.kie.client_for", return_value=client),
        )

    def test_stores_both_turns_and_sanitises_the_reply(self):
        with self._kie({"choices": [{"message": {"content": "<p>Hi</p><script>bad()</script>"}}]}):
            question, answer = engine.send_message(
                user=self.user, conversation=self.conversation, text="Hello there"
            )

        self.assertEqual(question.role, Message.Role.USER)
        self.assertEqual(answer.role, Message.Role.ASSISTANT)
        self.assertNotIn("script", answer.content_html)
        self.assertIn("<p>Hi</p>", answer.content_html)
        self.assertEqual(self.conversation.messages.count(), 2)

    def test_names_an_untitled_conversation_after_the_first_message(self):
        with self._kie({"choices": [{"message": {"content": "ok"}}]}):
            engine.send_message(
                user=self.user, conversation=self.conversation, text="What is a haiku?"
            )

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.title, "What is a haiku?")

    def test_an_api_failure_is_stored_as_a_failed_turn(self):
        from apps.common.services import kie

        with self._kie(error=kie.KieError("Out of credits")):
            _, answer = engine.send_message(
                user=self.user, conversation=self.conversation, text="Hello"
            )

        self.assertEqual(answer.status, Message.Status.FAILED)
        self.assertIn("Out of credits", answer.content_html)
        # The question survives, so the thread still makes sense on reload.
        self.assertEqual(self.conversation.messages.count(), 2)

    def test_an_empty_reply_is_reported_rather_than_stored_blank(self):
        with self._kie({"choices": []}):
            _, answer = engine.send_message(
                user=self.user, conversation=self.conversation, text="Hello"
            )

        self.assertEqual(answer.status, Message.Status.FAILED)
        self.assertIn("empty response", answer.content_html)

    def test_history_replays_earlier_turns(self):
        Message.objects.create(
            conversation=self.conversation, role=Message.Role.USER, content="First"
        )
        Message.objects.create(
            conversation=self.conversation, role=Message.Role.ASSISTANT, content="Second"
        )
        Message.objects.create(
            conversation=self.conversation,
            role=Message.Role.ASSISTANT,
            content="",
            status=Message.Status.FAILED,
        )

        turns = engine.build_history(self.conversation)
        self.assertEqual([turn.text for turn in turns], ["First", "Second"])

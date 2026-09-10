"""Outbound Telegram notifications.

When a user connects a bot token and chat id on the settings page, every
completed response is mirrored to Telegram.  Delivery is best-effort: a failing
notification must never take down the request that produced it.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Telegram rejects messages longer than 4096 characters.
MESSAGE_LIMIT = 4000


class TelegramError(Exception):
    """A Telegram API call failed."""


def _api(token: str, method: str) -> str:
    return f"{settings.TELEGRAM_API_BASE.rstrip('/')}/bot{token}/{method}"


def send_message(token: str, chat_id: str, text: str, *, silent: bool = False) -> bool:
    """Send a message, returning whether Telegram accepted it."""
    if not (token and chat_id and text):
        return False

    try:
        response = requests.post(
            _api(token, "sendMessage"),
            json={
                "chat_id": chat_id,
                "text": text[:MESSAGE_LIMIT],
                "parse_mode": "HTML",
                "disable_notification": silent,
                "link_preview_options": {"is_disabled": True},
            },
            timeout=15,
        )
    except requests.RequestException:
        logger.warning("Could not reach Telegram", exc_info=True)
        return False

    if not response.ok:
        logger.warning("Telegram rejected a message: %s", response.text[:300])
        return False
    return True


def notify(user_settings, text: str) -> bool:
    """Send a notification for a user, if they have the bridge switched on."""
    if user_settings is None or not user_settings.telegram_active:
        return False
    return send_message(user_settings.telegram_bot_token, user_settings.telegram_chat_id, text)


def describe_bot(token: str) -> dict:
    """Look up a bot's identity, used to validate a token on the settings page."""
    try:
        response = requests.get(_api(token, "getMe"), timeout=15)
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError(f"Could not reach Telegram: {exc}") from exc

    if not payload.get("ok"):
        raise TelegramError(payload.get("description") or "Telegram rejected this bot token.")
    return payload.get("result") or {}


def resolve_chat_id(token: str) -> str:
    """Find the most recent chat that has messaged the bot.

    Telegram will not tell a bot who its users are, so the only way to learn a
    chat id without asking for it is to read pending updates - which requires
    the user to have sent the bot a message first.
    """
    try:
        response = requests.get(_api(token, "getUpdates"), params={"limit": 10}, timeout=15)
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError(f"Could not reach Telegram: {exc}") from exc

    if not payload.get("ok"):
        raise TelegramError(payload.get("description") or "Telegram rejected this bot token.")

    for update in reversed(payload.get("result") or []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if chat.get("id") is not None:
            return str(chat["id"])

    raise TelegramError(
        "No conversation found. Send your bot a message on Telegram first, then try again."
    )

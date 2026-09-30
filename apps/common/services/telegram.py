"""Outbound Telegram notifications.

When a user connects a bot token and chat id on the settings page, every
completed response is mirrored to Telegram, and finished generations are sent
as the media itself.  Delivery is best-effort: a failing notification must
never take down the request that produced it.
"""

from __future__ import annotations

import html
import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Telegram rejects messages longer than 4096 characters, captions past 1024.
MESSAGE_LIMIT = 4000
CAPTION_LIMIT = 1024

# The Bot API method for each kind of media. Sent by URL, which Telegram
# fetches itself: up to 5 MB for photos and 20 MB for everything else.
MEDIA_METHODS = {
    "photo": "sendPhoto",
    "video": "sendVideo",
    "audio": "sendAudio",
    "document": "sendDocument",
}

# The update types that carry the chat someone reached the bot from.  Pressing
# Start sends a message, but adding the bot to a group only produces a
# membership change, so both are read.
CHAT_UPDATE_KEYS = ("message", "edited_message", "channel_post", "my_chat_member")

_TAG = re.compile(r"<[^>]+>")


class TelegramError(Exception):
    """A Telegram API call failed."""


def _api(token: str, method: str) -> str:
    return f"{settings.TELEGRAM_API_BASE.rstrip('/')}/bot{token}/{method}"


def send_message(token: str, chat_id: str, text: str, *, silent: bool = False) -> bool:
    """Send a message, returning whether Telegram accepted it."""
    if not (token and chat_id and text):
        return False

    response = _post_message(token, chat_id, text[:MESSAGE_LIMIT], silent=silent, parse_mode="HTML")
    if response is not None and _is_markup_error(response):
        # Truncation can split a tag or an entity, and a reply can contain
        # something Telegram's HTML subset refuses. Losing the formatting is
        # better than losing the notification.
        response = _post_message(
            token, chat_id, _plain(text)[:MESSAGE_LIMIT], silent=silent, parse_mode=None
        )
    return _accepted(response, "a message")


def send_media(token: str, chat_id: str, kind: str, *, url: str, caption: str = "") -> bool:
    """Send a photo, video, audio file or document from a public URL.

    Telegram downloads the file itself, so the URL has to be reachable from
    the internet. Returns whether Telegram accepted it.
    """
    if kind not in MEDIA_METHODS or not (token and chat_id and url):
        return False
    method = MEDIA_METHODS[kind]

    fields = {"chat_id": chat_id, kind: url}
    if caption:
        fields.update(caption=caption[:CAPTION_LIMIT], parse_mode="HTML")

    response = _post_media(token, method, fields)
    if response is not None and caption and _is_markup_error(response):
        fields.update(caption=_plain(caption)[:CAPTION_LIMIT])
        fields.pop("parse_mode")
        response = _post_media(token, method, fields)
    return _accepted(response, f"a {kind}")


def _post_media(token: str, method: str, fields: dict):
    try:
        # Telegram downloads the URL before answering, so allow for a big file.
        return requests.post(_api(token, method), json=fields, timeout=60)
    except requests.RequestException:
        logger.warning("Could not reach Telegram", exc_info=True)
        return None


def _accepted(response, what: str) -> bool:
    if response is None:
        return False
    if not response.ok:
        logger.warning("Telegram rejected %s: %s", what, response.text[:300])
        return False
    return True


def _plain(text: str) -> str:
    return html.unescape(_TAG.sub("", text))


def _post_message(token: str, chat_id: str, text: str, *, silent: bool, parse_mode: str | None):
    body = {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": silent,
        "link_preview_options": {"is_disabled": True},
    }
    if parse_mode:
        body["parse_mode"] = parse_mode
    try:
        return requests.post(_api(token, "sendMessage"), json=body, timeout=15)
    except requests.RequestException:
        logger.warning("Could not reach Telegram", exc_info=True)
        return None


def _is_markup_error(response) -> bool:
    return response.status_code == 400 and "parse entities" in response.text


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
        # A negative offset reads from the newest end; without it Telegram hands
        # back the oldest pending updates and a recent Start could be missed.
        response = requests.get(
            _api(token, "getUpdates"), params={"offset": -10, "limit": 10}, timeout=15
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError(f"Could not reach Telegram: {exc}") from exc

    if not payload.get("ok"):
        if payload.get("error_code") == 409:
            # A webhook and getUpdates are mutually exclusive, and removing
            # someone's webhook is not ours to do silently.
            raise TelegramError(
                "This bot has a webhook set, so its messages cannot be read here. "
                "Remove the webhook or use a dedicated bot, or enter the chat ID by hand."
            )
        raise TelegramError(payload.get("description") or "Telegram rejected this bot token.")

    for update in reversed(payload.get("result") or []):
        for key in CHAT_UPDATE_KEYS:
            chat = (update.get(key) or {}).get("chat") or {}
            if chat.get("id") is not None:
                return str(chat["id"])

    raise TelegramError(
        "No conversation found yet. Open your bot on Telegram, press Start, then press Detect."
    )

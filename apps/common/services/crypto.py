"""Encryption for credentials held on a user's behalf.

API keys are stored encrypted so that a leaked database dump does not hand out
everyone's KIE and Telegram credentials.  The key comes from
``CREDENTIALS_ENCRYPTION_KEY``; without it a per-process key is generated, which
keeps development working but makes stored secrets unreadable after a restart.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)

PREFIX = "enc:v1:"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    configured = (settings.CREDENTIALS_ENCRYPTION_KEY or "").strip()
    if configured:
        try:
            return Fernet(configured.encode())
        except (ValueError, TypeError):
            # Accept an arbitrary passphrase too, so operators are not forced to
            # produce a Fernet key by hand.
            digest = hashlib.sha256(configured.encode()).digest()
            return Fernet(base64.urlsafe_b64encode(digest))

    logger.warning(
        "CREDENTIALS_ENCRYPTION_KEY is not set; stored credentials will not "
        "survive a restart. Set it before deploying."
    )
    return Fernet(Fernet.generate_key())


def encrypt(value: str) -> str:
    """Encrypt a secret for storage.  Empty input stays empty."""
    if not value:
        return ""
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Reverse :func:`encrypt`, returning ``""`` for anything unreadable."""
    if not value:
        return ""
    if not value.startswith(PREFIX):
        # Written before encryption was enabled, or by a different key.
        return value
    try:
        return _fernet().decrypt(value.removeprefix(PREFIX).encode()).decode()
    except InvalidToken:
        logger.warning("Stored credential could not be decrypted with the current key")
        return ""


def mask(value: str, visible: int = 4) -> str:
    """Render a secret for display, e.g. ``sk-1234...cdef``."""
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "•" * len(value)
    return f"{value[:visible]}{'•' * 8}{value[-visible:]}"

"""HMAC-SHA256 signing with domain separation, from the standard library only.

Why not a JWT library: the tokens here are only ever produced and consumed by this service, so a
header-less format removes the whole class of algorithm-confusion bugs (`alg: none`, RS/HS swaps) and adds no
dependency. Each purpose (session, confirmation, audit hashing) derives its own key from SESSION_SECRET, so a
token minted for one purpose can never verify under another.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

MIN_SECRET_BYTES = 32
SECRET_ENV = "SESSION_SECRET"


class SecretMissingError(RuntimeError):
    """SESSION_SECRET is absent or too short. The message never includes the value."""


def load_secret(env_file: str | Path = ".env") -> bytes:
    """Read SESSION_SECRET from the environment, falling back to the git-ignored .env file."""
    from data_engineering.pipelines.env import load_env_file

    load_env_file(env_file)
    value = os.environ.get(SECRET_ENV, "")
    if len(value.encode()) < MIN_SECRET_BYTES:
        raise SecretMissingError(
            f"{SECRET_ENV} must be set (at least {MIN_SECRET_BYTES} bytes). Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    return value.encode()


def canonical_json(value: Any) -> bytes:
    """Stable serialization: sorted keys, no whitespace, non-JSON types rendered with str()."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Signer:
    """Signs and verifies small JSON payloads for one purpose."""

    def __init__(self, secret: bytes, purpose: str) -> None:
        if len(secret) < MIN_SECRET_BYTES:
            raise SecretMissingError(f"secret shorter than {MIN_SECRET_BYTES} bytes")
        self._key = hmac.new(secret, f"cautela:{purpose}".encode(), hashlib.sha256).digest()

    def digest(self, value: Any) -> str:
        """Keyed hash of a value. Keyed so low-entropy inputs (document numbers) cannot be brute-forced."""
        return hmac.new(self._key, canonical_json(value), hashlib.sha256).hexdigest()

    def sign(self, payload: dict[str, Any]) -> str:
        body = b64e(canonical_json(payload))
        mac = hmac.new(self._key, body.encode(), hashlib.sha256).digest()
        return f"{body}.{b64e(mac)}"

    def verify(self, token: str) -> dict[str, Any] | None:
        """Return the payload if the signature is valid, else None. Constant-time comparison."""
        if not isinstance(token, str) or token.count(".") != 1:
            return None
        body, mac = token.split(".")
        try:
            given = b64d(mac)
            expected = hmac.new(self._key, body.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(given, expected):
                return None
            payload = json.loads(b64d(body))
        except (ValueError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

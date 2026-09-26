"""Test identity service: two-step login and signed, expiring session tokens bound to one customer.

Flow:
  1. start_login(document_number)   -> a challenge id. A one-time code goes to the customer's registered
                                        channel (mock outbox). The document number alone grants nothing.
  2. verify_otp(challenge_id, code)  -> a session token bound to exactly one customer_id.
  3. validate(token)                 -> the Session, or AuthError (tampered, expired, revoked, not yet valid).

Each tool request also carries a request id that is accepted once per session (consume_request_id), so a
captured request cannot be replayed. Revoked sessions (logout) are rejected even before they expire.

Unknown documents get a challenge with the same shape as known ones and no code is sent, so the login step does
not reveal whether a document number belongs to a customer. This is a mock: codes, sessions and the outbox live
in memory, which is enough for a single-process prototype and is listed as deployment work in agent/README.md.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from agent.clock import Clock, system_clock
from agent.security.signing import Signer

SESSION_TTL = timedelta(minutes=15)
OTP_TTL = timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 3
OTP_DIGITS = 6
LOGIN_WINDOW = timedelta(minutes=15)
MAX_CHALLENGES_PER_WINDOW = 3  # per document; with 3 attempts each, at most 9 guesses per 15 minutes
CLOCK_SKEW = timedelta(seconds=30)
TOKEN_VERSION = 1


class AuthError(Exception):
    """Authentication failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class DirectoryEntry:
    customer_id: str
    mobile_phone: str | None
    email: str | None
    customer_status: str


class CustomerDirectory(Protocol):
    def lookup_by_document(self, document_number: str) -> DirectoryEntry | None: ...


@dataclass
class MockChannel:
    """Stands in for SMS or email delivery. Tests read the outbox the way a customer reads their phone."""

    outbox: list[tuple[str, str, str]] = field(default_factory=list)  # (customer_id, destination, message)

    def send(self, customer_id: str, destination: str, message: str) -> None:
        self.outbox.append((customer_id, destination, message))

    def last_code_for(self, customer_id: str) -> str | None:
        for cid, _, message in reversed(self.outbox):
            if cid == customer_id:
                match = re.search(r"\b(\d{6})\b", message)
                return match.group(1) if match else None
        return None


@dataclass(frozen=True)
class LoginChallenge:
    challenge_id: str
    channel_hint: str
    expires_at: datetime


@dataclass(frozen=True)
class Session:
    session_id: str
    customer_id: str
    issued_at: datetime
    expires_at: datetime

    @property
    def customer_ref(self) -> str:
        """Session-scoped reference for handoffs and audit. The console resolves identity on its own side."""
        return f"session:{self.session_id}"


@dataclass(frozen=True)
class SessionGrant:
    token: str
    session: Session


@dataclass
class _Challenge:
    customer_id: str | None
    code_digest: str | None
    expires_at: datetime
    attempts: int = 0
    used: bool = False


def normalize_document(document_number: str) -> str:
    return re.sub(r"[\s.\-]", "", str(document_number)).upper()


class IdentityService:
    ACTIVE_STATUSES = frozenset({"Active"})

    def __init__(self, secret: bytes, directory: CustomerDirectory, channel: MockChannel | None = None,
                 clock: Clock = system_clock, max_challenges: int = MAX_CHALLENGES_PER_WINDOW) -> None:
        if max_challenges < 1:
            raise ValueError("max_challenges must be at least 1")
        self._max_challenges = max_challenges
        self._tokens = Signer(secret, "session")
        self._otp = Signer(secret, "otp")
        self._directory = directory
        self.channel = channel or MockChannel()
        self._clock = clock
        self._challenges: dict[str, _Challenge] = {}
        self._revoked: set[str] = set()
        self._seen_requests: dict[str, set[str]] = {}
        self._recent_logins: dict[str, list[datetime]] = {}

    # ---- step 1: the document number only starts a challenge ------------------------------------------
    def start_login(self, document_number: str) -> LoginChallenge:
        now = self._clock()
        self._prune(now)
        document = normalize_document(document_number)
        entry = self._directory.lookup_by_document(document)
        destination = None
        if entry and entry.customer_status in self.ACTIVE_STATUSES and not self._throttled(document, now):
            destination = entry.mobile_phone or entry.email
        challenge_id = secrets.token_urlsafe(16)
        # The code and its digest are computed on every path so timing does not reveal whether the document
        # exists; only a real destination receives the code. A throttled document silently gets no code.
        code = f"{secrets.randbelow(10 ** OTP_DIGITS):0{OTP_DIGITS}d}"
        code_digest = self._otp.digest([challenge_id, code]) if destination else None
        if not destination:
            self._otp.digest([challenge_id, "dummy"])
        else:
            self.channel.send(entry.customer_id, destination, f"Cautela: tu codigo es {code}. Vence en 5 minutos.")
        self._challenges[challenge_id] = _Challenge(
            customer_id=entry.customer_id if destination else None, code_digest=code_digest,
            expires_at=now + OTP_TTL,
        )
        return LoginChallenge(challenge_id, "registered_channel", now + OTP_TTL)

    def _throttled(self, document: str, now: datetime) -> bool:
        """Cap challenges per document so the 6-digit code cannot be brute-forced across fresh challenges."""
        recent = [t for t in self._recent_logins.get(document, []) if now - t < LOGIN_WINDOW]
        throttled = len(recent) >= self._max_challenges
        self._recent_logins[document] = recent if throttled else [*recent, now]
        return throttled

    def _prune(self, now: datetime) -> None:
        """Drop expired challenges so the in-memory store stays bounded."""
        for key in [k for k, c in self._challenges.items() if now > c.expires_at]:
            del self._challenges[key]

    # ---- step 2: the second factor ----------------------------------------------------------------------
    def verify_otp(self, challenge_id: str, code: str) -> SessionGrant:
        now = self._clock()
        challenge = self._challenges.get(challenge_id)
        if challenge is None or challenge.used:
            raise AuthError("otp_invalid", "unknown or already used challenge")
        if now > challenge.expires_at:
            raise AuthError("otp_expired")
        if challenge.attempts >= OTP_MAX_ATTEMPTS:
            raise AuthError("otp_locked")
        challenge.attempts += 1
        given = self._otp.digest([challenge_id, str(code).strip()])
        if challenge.code_digest is None or not secrets.compare_digest(given, challenge.code_digest):
            if challenge.attempts >= OTP_MAX_ATTEMPTS:
                challenge.used = True
            raise AuthError("otp_invalid")
        challenge.used = True
        return self._issue(challenge.customer_id, now)

    def _issue(self, customer_id: str, now: datetime) -> SessionGrant:
        session = Session(secrets.token_urlsafe(12), customer_id, now, now + SESSION_TTL)
        token = self._tokens.sign({
            "v": TOKEN_VERSION, "sid": session.session_id, "sub": customer_id,
            "iat": int(now.timestamp()), "exp": int(session.expires_at.timestamp()),
        })
        return SessionGrant(token, session)

    # ---- every request ---------------------------------------------------------------------------------
    def validate(self, token: str) -> Session:
        payload = self._tokens.verify(token)
        if payload is None or payload.get("v") != TOKEN_VERSION:
            raise AuthError("session_invalid", "signature check failed")
        try:
            sid, sub = str(payload["sid"]), str(payload["sub"])
            iat = datetime.fromtimestamp(int(payload["iat"]), tz=self._clock().tzinfo)
            exp = datetime.fromtimestamp(int(payload["exp"]), tz=self._clock().tzinfo)
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthError("session_invalid", "malformed payload") from exc
        now = self._clock()
        if sid in self._revoked:
            raise AuthError("session_revoked")
        if iat > now + CLOCK_SKEW:
            raise AuthError("session_invalid", "issued in the future")
        if now >= exp:
            raise AuthError("session_expired")
        return Session(sid, sub, iat, exp)

    def consume_request_id(self, session: Session, request_id: str) -> None:
        """Accept each request id once per session. A second use is a replay."""
        if not request_id or len(request_id) > 128:
            raise AuthError("request_id_invalid")
        seen = self._seen_requests.setdefault(session.session_id, set())
        if request_id in seen:
            raise AuthError("replay_detected")
        seen.add(request_id)

    def revoke(self, token: str) -> None:
        session = self.validate(token)
        self._revoked.add(session.session_id)
        self._seen_requests.pop(session.session_id, None)

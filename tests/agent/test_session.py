"""Identity: two-factor login, signed expiring tokens, tampering, replay and revocation."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import pytest

from agent.clock import FrozenClock
from agent.security.session import (
    OTP_MAX_ATTEMPTS,
    AuthError,
    DirectoryEntry,
    IdentityService,
    MockChannel,
)
from agent.security.signing import SecretMissingError, Signer, b64d, b64e, load_secret

DOC = "1023456789"


class FakeDirectory:
    def __init__(self) -> None:
        self.entries = {
            DOC: DirectoryEntry("C000001", "+57 300 123 4567", None, "Active"),
            "2000000002": DirectoryEntry("C000002", None, None, "Active"),
            "3000000003": DirectoryEntry("C000003", "+52 55 1234 5678", None, "Suspended"),
        }

    def lookup_by_document(self, document_number: str) -> DirectoryEntry | None:
        return self.entries.get(document_number)


@pytest.fixture()
def clock():
    return FrozenClock(datetime(2026, 6, 1, 12, 0, tzinfo=UTC))


@pytest.fixture()
def idp(clock):
    return IdentityService(secrets.token_bytes(48), FakeDirectory(), MockChannel(), clock)


def login(idp: IdentityService, doc: str = DOC, customer_id: str = "C000001"):
    challenge = idp.start_login(doc)
    return idp.verify_otp(challenge.challenge_id, idp.channel.last_code_for(customer_id))


def test_login_with_second_factor_binds_session_to_one_customer(idp):
    grant = login(idp)
    session = idp.validate(grant.token)
    assert session.customer_id == "C000001"
    assert session.customer_ref == f"session:{session.session_id}"


def test_document_number_alone_never_authenticates(idp):
    challenge = idp.start_login(DOC)
    with pytest.raises(AuthError) as exc:
        idp.verify_otp(challenge.challenge_id, DOC)  # the document number is not the code
    assert exc.value.code == "otp_invalid"
    assert not hasattr(idp, "login_with_document")


def test_document_with_dots_and_spaces_is_normalized(idp):
    idp.start_login("10.234.567 89")
    assert idp.channel.last_code_for("C000001") is not None


def test_unknown_document_gets_same_shaped_challenge_and_no_code(idp):
    known, unknown = idp.start_login(DOC), idp.start_login("9999999999")
    assert known.channel_hint == unknown.channel_hint == "registered_channel"
    assert len(idp.channel.outbox) == 1  # only the known customer received a code
    with pytest.raises(AuthError):
        idp.verify_otp(unknown.challenge_id, "000000")


@pytest.mark.parametrize("doc", ["2000000002", "3000000003"], ids=["no_registered_channel", "suspended"])
def test_customers_without_channel_or_not_active_cannot_log_in(idp, doc):
    challenge = idp.start_login(doc)
    assert idp.channel.outbox == []
    with pytest.raises(AuthError):
        idp.verify_otp(challenge.challenge_id, "123456")


def test_otp_is_single_use(idp):
    challenge = idp.start_login(DOC)
    code = idp.channel.last_code_for("C000001")
    idp.verify_otp(challenge.challenge_id, code)
    with pytest.raises(AuthError) as exc:
        idp.verify_otp(challenge.challenge_id, code)
    assert exc.value.code == "otp_invalid"


def test_otp_expires(idp, clock):
    challenge = idp.start_login(DOC)
    clock.advance(minutes=6)
    with pytest.raises(AuthError) as exc:
        idp.verify_otp(challenge.challenge_id, idp.channel.last_code_for("C000001"))
    assert exc.value.code == "otp_expired"


def test_otp_locks_after_max_attempts_even_with_right_code(idp):
    challenge = idp.start_login(DOC)
    for _ in range(OTP_MAX_ATTEMPTS):
        with pytest.raises(AuthError):
            idp.verify_otp(challenge.challenge_id, "000000x")
    with pytest.raises(AuthError):
        idp.verify_otp(challenge.challenge_id, idp.channel.last_code_for("C000001"))


def test_expired_session_is_rejected(idp, clock):
    grant = login(idp)
    clock.advance(minutes=15)
    with pytest.raises(AuthError) as exc:
        idp.validate(grant.token)
    assert exc.value.code == "session_expired"


def test_token_issued_in_the_future_is_rejected(idp, clock):
    grant = login(idp)
    clock.advance(minutes=-5)
    with pytest.raises(AuthError) as exc:
        idp.validate(grant.token)
    assert exc.value.code == "session_invalid"


def _tamper_subject(token: str, new_sub: str) -> str:
    import json
    body, mac = token.split(".")
    payload = json.loads(b64d(body))
    payload["sub"] = new_sub
    return b64e(json.dumps(payload).encode()) + "." + mac


@pytest.mark.parametrize("mutate", [
    lambda t: _tamper_subject(t, "C000002"),                    # swap the customer, keep the signature
    lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),  # corrupt the signature
    lambda t: t.split(".")[0],                                   # drop the signature
    lambda t: "",
    lambda t: "not.a.token",
], ids=["customer_swapped", "signature_corrupted", "signature_missing", "empty", "garbage"])
def test_tampered_token_is_rejected(idp, mutate):
    grant = login(idp)
    with pytest.raises(AuthError) as exc:
        idp.validate(mutate(grant.token))
    assert exc.value.code == "session_invalid"


def test_token_signed_with_another_secret_is_rejected(idp, clock):
    other = IdentityService(secrets.token_bytes(48), FakeDirectory(), MockChannel(), clock)
    with pytest.raises(AuthError):
        idp.validate(login(other).token)


def test_token_minted_for_another_purpose_does_not_verify_as_session(idp):
    secret = secrets.token_bytes(48)
    forged = Signer(secret, "confirmation").sign({"v": 1, "sid": "x", "sub": "C000001", "iat": 0, "exp": 2**40})
    service = IdentityService(secret, FakeDirectory())
    with pytest.raises(AuthError):
        service.validate(forged)


def test_request_id_replay_is_rejected(idp):
    session = idp.validate(login(idp).token)
    idp.consume_request_id(session, "req-1")
    with pytest.raises(AuthError) as exc:
        idp.consume_request_id(session, "req-1")
    assert exc.value.code == "replay_detected"


def test_revoked_session_token_replay_is_rejected(idp):
    grant = login(idp)
    idp.revoke(grant.token)
    with pytest.raises(AuthError) as exc:
        idp.validate(grant.token)
    assert exc.value.code == "session_revoked"


def test_secret_loader_refuses_short_or_missing_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_SECRET", "")  # recorded, so monkeypatch restores it afterwards
    with pytest.raises(SecretMissingError) as exc:
        load_secret(tmp_path / "absent.env")
    monkeypatch.setenv("SESSION_SECRET", "short")
    with pytest.raises(SecretMissingError) as exc2:
        load_secret(tmp_path / "absent.env")
    assert "short" not in str(exc2.value) and "SESSION_SECRET" in str(exc.value)


def test_secret_loader_reads_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_SECRET", "")  # recorded, so monkeypatch restores it afterwards
    env = tmp_path / ".env"
    env.write_text("SESSION_SECRET=" + "k" * 40 + "\n", encoding="utf-8")
    assert load_secret(env) == b"k" * 40


def test_fresh_challenges_are_throttled_per_document(idp, clock):
    from agent.security.session import MAX_CHALLENGES_PER_WINDOW
    for _ in range(MAX_CHALLENGES_PER_WINDOW):
        idp.start_login(DOC)
    sent = len(idp.channel.outbox)
    blocked = idp.start_login(DOC)  # same response shape, but no code is sent
    assert len(idp.channel.outbox) == sent and blocked.channel_hint == "registered_channel"
    with pytest.raises(AuthError):
        idp.verify_otp(blocked.challenge_id, idp.channel.last_code_for("C000001"))
    clock.advance(minutes=16)
    idp.start_login(DOC)
    assert len(idp.channel.outbox) == sent + 1

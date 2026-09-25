"""Permission layer through the service: sessions, cross-customer access, allowlist, confirmations, audit."""

from __future__ import annotations

import pytest

from agent.policy.engine import READ_ACTIONS


def _deny_code(result) -> str:
    assert not result.ok and result.error is not None
    return result.error.code


def _open_args(tx_id: str, key: str = "idem-key-0001") -> dict:
    return {"transaction_id": tx_id, "idempotency_key": key, "customer_statement": "No reconozco este cargo"}


# ---- sessions ------------------------------------------------------------------------------------------
def test_expired_session_is_denied_and_audited(rig, people):
    token = rig.login(people["alice"])
    rig.clock.advance(minutes=16)
    result = rig.call("get_customer_profile", {}, token)
    assert _deny_code(result) == "session_expired"
    assert rig.audit.records(result.trace_id)[-1].outcome == "denied"


def test_tampered_token_is_denied(rig, people):
    token = rig.login(people["alice"])
    assert _deny_code(rig.call("get_customer_profile", {}, token[:-3] + "xyz")) == "session_invalid"


def test_replayed_request_is_denied_and_flags_the_session(rig, people, purchases):
    token = rig.login(people["alice"])
    first = rig.service.execute("get_customer_profile", {}, token, "same-request-id")
    replay = rig.service.execute("get_customer_profile", {}, token, "same-request-id")
    assert first.ok and _deny_code(replay) == "replay_detected"
    assert replay.handoff_required and replay.handoff_reason == "security_event"
    tx_id = purchases["alice"][0]["transaction_id"]
    later = rig.call("open_dispute_case", _open_args(tx_id), token)
    assert _deny_code(later) == "policy_denied" and "SYN-SEC-001" in later.rule_ids


def test_logged_out_session_cannot_be_reused(rig, people):
    token = rig.login(people["alice"])
    rig.identity.revoke(token)
    assert _deny_code(rig.call("get_customer_profile", {}, token)) == "session_revoked"


def test_unknown_tool_is_denied_by_default(rig, people):
    token = rig.login(people["alice"])
    for tool in ("transfer_money", "refund", "get_customer_profile ", "__import__"):
        assert _deny_code(rig.call(tool, {}, token)) == "tool_not_allowed"


# ---- ownership, on every tool ----------------------------------------------------------------------------
@pytest.fixture()
def bob_records(rig, people, purchases):
    """A dispute case opened by Bob, plus his transaction and card ids."""
    bob = rig.login(people["bob"])
    tx_id = purchases["bob"][0]["transaction_id"]
    case = rig.confirm_and_call("open_dispute_case", _open_args(tx_id, "bob-key-0001"), bob)
    assert case.ok, case.error
    return {"transaction_id": tx_id, "product_id": people["bob"]["card_id"], "case_id": case.data["case_id"]}


CROSS_CALLS = [
    ("get_transaction", lambda r: {"transaction_id": r["transaction_id"]}),
    ("get_dispute_policy", lambda r: {"transaction_id": r["transaction_id"]}),
    ("open_dispute_case", lambda r: _open_args(r["transaction_id"], "alice-key-9999")),
    ("block_card", lambda r: {"product_id": r["product_id"]}),
    ("get_case_status", lambda r: {"case_id": r["case_id"]}),
]


@pytest.mark.parametrize(("tool", "make_args"), CROSS_CALLS, ids=[c[0] for c in CROSS_CALLS])
def test_cross_customer_access_is_denied_as_not_found(rig, people, bob_records, tool, make_args):
    alice = rig.login(people["alice"])
    result = rig.call(tool, make_args(bob_records), alice)
    assert _deny_code(result) == "not_found"
    assert result.handoff_required and result.handoff_reason == "security_event"
    record = rig.audit.records(result.trace_id)[-1]
    assert record.outcome == "denied" and record.reason == "ownership_violation"


@pytest.mark.parametrize(("tool", "make_args"), CROSS_CALLS[2:4], ids=["open_dispute_case", "block_card"])
def test_cross_customer_confirmation_cannot_be_obtained(rig, people, bob_records, tool, make_args):
    alice = rig.login(people["alice"])
    challenge = rig.service.request_confirmation(alice, rig.rid(), tool, make_args(bob_records))
    assert _deny_code(challenge) == "not_found"


def test_missing_record_and_foreign_record_look_the_same(rig, people, bob_records):
    alice = rig.login(people["alice"])
    missing = rig.call("get_transaction", {"transaction_id": "TX99999999"}, alice)
    foreign = rig.call("get_transaction", {"transaction_id": bob_records["transaction_id"]}, alice)
    assert missing.error == foreign.error
    assert not missing.handoff_required  # only the real cross-customer attempt is a security event


@pytest.mark.parametrize("tool", ["get_customer_profile", "list_recent_transactions", "find_candidate_charges"])
def test_customer_id_cannot_be_injected_into_tools_without_record_ids(rig, people, tool):
    alice = rig.login(people["alice"])
    args = {"customer_id": people["bob"]["customer_id"]}
    if tool == "find_candidate_charges":
        args["amount"] = 100.0
    result = rig.call(tool, args, alice)
    assert _deny_code(result) == "validation_error"


def test_reads_without_record_ids_only_return_own_data(rig, people):
    alice = rig.login(people["alice"])
    listed = rig.call("list_recent_transactions", {"window_days": 90, "limit": 50}, alice)
    ids = [t["transaction_id"] for t in listed.data["transactions"]]
    owners = {rig.repo.owner_of("transaction", i) for i in ids}
    assert ids and owners == {people["alice"]["customer_id"]}
    candidates = rig.call("find_candidate_charges", {"amount": 100.0}, alice)
    assert {rig.repo.owner_of("transaction", c["transaction"]["transaction_id"])
            for c in candidates.data["candidates"]} <= {people["alice"]["customer_id"]}


# ---- confirmations ------------------------------------------------------------------------------------
def test_write_without_confirmation_is_denied(rig, people, purchases):
    alice = rig.login(people["alice"])
    result = rig.call("open_dispute_case", _open_args(purchases["alice"][0]["transaction_id"]), alice)
    assert _deny_code(result) == "confirmation_required" and result.rule_ids == ["SYN-CONFIRM-001"]
    assert rig.cases.find_active_case(people["alice"]["customer_id"], purchases["alice"][0]["transaction_id"]) \
        is None


def test_confirmation_is_single_use(rig, people, purchases):
    alice = rig.login(people["alice"])
    args = _open_args(purchases["alice"][0]["transaction_id"])
    challenge = rig.service.request_confirmation(alice, rig.rid(), "open_dispute_case", args)
    assert rig.call("open_dispute_case", args, alice, challenge.token).ok
    reused = rig.call("open_dispute_case", args, alice, challenge.token)
    assert _deny_code(reused) == "confirmation_invalid"
    assert rig.audit.records(reused.trace_id)[-1].reason == "confirmation_already_used"


def test_confirmation_is_bound_to_exact_arguments(rig, people, purchases):
    alice = rig.login(people["alice"])
    first, second = purchases["alice"][0]["transaction_id"], purchases["alice"][1]["transaction_id"]
    challenge = rig.service.request_confirmation(alice, rig.rid(), "open_dispute_case", _open_args(first))
    swapped = rig.call("open_dispute_case", _open_args(second), alice, challenge.token)
    assert _deny_code(swapped) == "confirmation_invalid"
    assert rig.audit.records(swapped.trace_id)[-1].reason == "confirmation_args_mismatch"
    edited = {**_open_args(first), "customer_statement": "otro texto"}
    assert _deny_code(rig.call("open_dispute_case", edited, alice, challenge.token)) == "confirmation_invalid"


def test_confirmation_is_bound_to_tool_session_and_time(rig, people, purchases):
    alice = rig.login(people["alice"])
    args = _open_args(purchases["alice"][0]["transaction_id"])
    challenge = rig.service.request_confirmation(alice, rig.rid(), "open_dispute_case", args)
    other_session = rig.login(people["alice"])
    assert _deny_code(rig.call("open_dispute_case", args, other_session, challenge.token)) == "confirmation_invalid"
    block = rig.call("block_card", {"product_id": people["alice"]["card_id"]}, alice, challenge.token)
    assert _deny_code(block) == "confirmation_invalid"
    rig.clock.advance(minutes=6)
    fresh = rig.login(people["alice"])
    late = rig.service.request_confirmation(fresh, rig.rid(), "open_dispute_case", args)
    rig.clock.advance(minutes=5)
    assert _deny_code(rig.call("open_dispute_case", args, fresh, late.token)) == "confirmation_invalid"


def test_tampered_confirmation_is_rejected(rig, people, purchases):
    alice = rig.login(people["alice"])
    args = _open_args(purchases["alice"][0]["transaction_id"])
    challenge = rig.service.request_confirmation(alice, rig.rid(), "open_dispute_case", args)
    assert _deny_code(rig.call("open_dispute_case", args, alice, challenge.token + "x")) == "confirmation_invalid"


def test_confirmation_not_issued_for_policy_denied_action(rig, people, query):
    declined = query("SELECT transaction_id FROM silver.transactions WHERE customer_id = ? AND "
                     "transaction_status = 'Declined' LIMIT 1", [people["alice"]["customer_id"]])
    if not declined:
        pytest.skip("fixture customer has no declined transaction")
    alice = rig.login(people["alice"])
    result = rig.service.request_confirmation(alice, rig.rid(), "open_dispute_case",
                                              _open_args(declined[0]["transaction_id"]))
    assert _deny_code(result) == "policy_denied" and "SYN-STATUS-002" in result.rule_ids


def test_reads_do_not_need_confirmation(rig, people):
    alice = rig.login(people["alice"])
    assert rig.call("get_customer_profile", {}, alice).ok
    assert "get_customer_profile" in READ_ACTIONS


# ---- audit coverage -------------------------------------------------------------------------------------
def test_every_call_including_denials_is_audited(rig, people, purchases):
    alice = rig.login(people["alice"])
    results = [
        rig.call("get_customer_profile", {}, alice),
        rig.call("nope", {}, alice),
        rig.call("get_transaction", {"transaction_id": "bad id!"}, alice),
        rig.call("open_dispute_case", _open_args(purchases["alice"][0]["transaction_id"]), alice),
        rig.call("get_customer_profile", {}, "garbage"),
    ]
    for result in results:
        records = rig.audit.records(result.trace_id)
        assert records, result
        assert all(r.trace_id == result.trace_id for r in records)
    assert [rig.audit.records(r.trace_id)[-1].outcome for r in results] == \
        ["ok", "denied", "denied", "denied", "denied"]
    assert rig.audit.verify_chain()
    assert all(r.latency_ms >= 0 for r in rig.audit.records())

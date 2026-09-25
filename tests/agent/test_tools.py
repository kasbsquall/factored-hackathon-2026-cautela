"""Tool behavior on the fixture warehouse: contracts, masking, writes, idempotency, verify step, retries."""

from __future__ import annotations

import duckdb
import pytest

from agent.tools.export_schemas import OUT_DIR, render
from agent.tools.registry import TOOLS


def _open_args(tx_id: str, key: str = "idem-key-0001", statement: str = "No reconozco este cargo") -> dict:
    return {"transaction_id": tx_id, "idempotency_key": key, "customer_statement": statement}


def _small_purchase(purchases, who="alice"):
    return next(p for p in purchases[who] if (p["amount_usd"] or 0) < 450 and (p["fraud_score"] or 0) < 50
                and not p["is_fraud"])


# ---- reads ----------------------------------------------------------------------------------------------
def test_profile_is_masked_and_minimal(rig, people, query):
    alice = rig.login(people["alice"])
    profile = rig.call("get_customer_profile", {}, alice).data
    raw = query("SELECT * FROM silver.customers WHERE customer_id = ?", [people["alice"]["customer_id"]])[0]
    text = str(profile)
    assert raw["document_number"] not in text and raw["last_name"] not in text
    assert (raw["email"] or "@@") not in text and (raw["mobile_phone"] or "@@") not in text
    assert "credit_score" not in profile and "estimated_monthly_income" not in profile
    assert all(len(p["product_number_masked"].strip("*")) == 4 for p in profile["products"])


def test_transaction_window_is_bounded(rig, people):
    alice = rig.login(people["alice"])
    assert rig.call("list_recent_transactions", {"window_days": 91}, alice).error.code == "validation_error"
    assert rig.call("list_recent_transactions", {"limit": 51}, alice).error.code == "validation_error"
    listed = rig.call("list_recent_transactions", {"window_days": 7, "limit": 3}, alice).data
    assert len(listed["transactions"]) <= 3
    assert all(t["transaction_date"] >= listed["window_start"][:19] for t in listed["transactions"])


def test_internal_fraud_signals_are_not_exposed(rig, people, purchases):
    alice = rig.login(people["alice"])
    view = rig.call("get_transaction", {"transaction_id": purchases["alice"][0]["transaction_id"]}, alice).data
    assert "fraud_score" not in view and "is_fraud" not in view


def test_find_candidate_charges_names_a_best_match_only_when_clear(rig, people, purchases):
    alice = rig.login(people["alice"])
    target = purchases["alice"][0]
    exact = rig.call("find_candidate_charges", {
        "amount": float(target["amount"]), "currency": target["currency"],
        "date_hint": target["transaction_date"].date().isoformat(), "merchant_hint": target["merchant_name"]},
        alice).data
    assert exact["best_transaction_id"] == target["transaction_id"] and not exact["is_ambiguous"]
    vague = rig.call("find_candidate_charges", {"date_hint": target["transaction_date"].date().isoformat(),
                                                "date_tolerance_days": 30}, alice).data
    assert vague["is_ambiguous"] and vague["best_transaction_id"] is None
    assert len(vague["candidates"]) > 1 and vague["ambiguity_rule"]


def test_find_candidate_charges_requires_a_hint(rig, people):
    alice = rig.login(people["alice"])
    assert rig.call("find_candidate_charges", {}, alice).error.code == "validation_error"


def test_dispute_policy_explains_with_rule_ids(rig, people, purchases):
    alice = rig.login(people["alice"])
    tx = _small_purchase(purchases)
    result = rig.call("get_dispute_policy", {"transaction_id": tx["transaction_id"]}, alice)
    decision = result.data["decision"]
    assert "open_dispute_case" in decision["allowed_actions"]
    assert {"MX-WINDOW-001", "SYN-CONFIRM-001"} <= {h["rule_id"] for h in decision["rules_fired"]}


# ---- writes -----------------------------------------------------------------------------------------------
def test_open_dispute_case_is_verified_and_never_touches_source(rig, people, purchases, warehouse):
    alice = rig.login(people["alice"])
    tx = _small_purchase(purchases)
    before = rig.repo._query("SELECT count(*) AS n FROM silver.transactions", [], faulted=False)[0]["n"]
    result = rig.confirm_and_call("open_dispute_case",
                                  _open_args(tx["transaction_id"], statement="mi tarjeta 4111111111111234"), alice)
    assert result.ok and result.verification == "verified" and result.data["status"] == "open"
    stored = rig.cases.read_case(result.data["case_id"])
    assert "4111111111111234" not in stored["statement_masked"]
    assert rig.repo._query("SELECT count(*) AS n FROM silver.transactions", [], faulted=False)[0]["n"] == before
    with pytest.raises(duckdb.Error):
        rig.repo._con.execute("DELETE FROM silver.transactions")
    status = rig.call("get_case_status", {"case_id": result.data["case_id"]}, alice)
    assert status.ok and status.data["transaction_id"] == tx["transaction_id"]


def test_open_dispute_case_is_idempotent(rig, people, purchases):
    alice = rig.login(people["alice"])
    tx = _small_purchase(purchases)
    first = rig.confirm_and_call("open_dispute_case", _open_args(tx["transaction_id"]), alice)
    again = rig.confirm_and_call("open_dispute_case", _open_args(tx["transaction_id"]), alice)
    assert again.ok and again.data["case_id"] == first.data["case_id"] and again.data["idempotent_replay"]
    other_key = rig.confirm_and_call("open_dispute_case", _open_args(tx["transaction_id"], "idem-key-0002"), alice)
    assert other_key.data["case_id"] == first.data["case_id"] and other_key.data["already_open"]
    rows = rig.cases._con.execute("SELECT count(*) FROM sandbox.dispute_cases").fetchone()[0]
    assert rows == 1


def test_idempotency_key_reused_with_other_arguments_conflicts(rig, people, purchases):
    alice = rig.login(people["alice"])
    a, b = purchases["alice"][0]["transaction_id"], purchases["alice"][1]["transaction_id"]
    assert rig.confirm_and_call("open_dispute_case", _open_args(a), alice).ok
    conflict = rig.confirm_and_call("open_dispute_case", _open_args(b), alice)
    assert conflict.error.code == "idempotency_conflict"


def test_high_amount_case_is_registered_for_human_review(rig, people, purchases):
    high = [p for p in purchases["alice"] if (p["amount_usd"] or 0) >= 450]
    if not high:
        pytest.skip("no high-amount purchase for this fixture customer")
    alice = rig.login(people["alice"])
    result = rig.confirm_and_call("open_dispute_case", _open_args(high[0]["transaction_id"]), alice)
    assert result.ok and result.data["status"] == "pending_human_review"
    assert "SYN-AMOUNT-001" in result.data["policy_rule_ids"]


def test_block_card_is_verified_and_overlays_status(rig, people):
    alice = rig.login(people["alice"])
    card = people["alice"]["card_id"]
    result = rig.confirm_and_call("block_card", {"product_id": card}, alice)
    assert result.ok and result.verification == "verified"
    profile = rig.call("get_customer_profile", {}, alice).data
    assert next(p for p in profile["products"] if p["product_id"] == card)["status"] == "Blocked"
    again = rig.service.request_confirmation(alice, rig.rid(), "block_card", {"product_id": card})
    assert again.error.code == "policy_denied"  # already blocked: nothing to confirm


# ---- verify step, retries and fallback ------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["stale_read", "lost_write"])
def test_verify_step_catches_writes_that_did_not_persist(rig, people, purchases, mode):
    alice = rig.login(people["alice"])
    op = "case_store.read" if mode == "stale_read" else "case_store.write"
    rig.faults.set(op, mode)
    result = rig.confirm_and_call("open_dispute_case", _open_args(_small_purchase(purchases)["transaction_id"]),
                                  alice)
    assert not result.ok and result.verification == "not_verified"
    assert result.handoff_required and result.handoff_reason == "tool_failure"
    assert rig.audit.records(result.trace_id)[-2].outcome == "not_verified"


def test_block_card_verify_catches_lost_write(rig, people):
    alice = rig.login(people["alice"])
    rig.faults.set("case_store.write", "lost_write")
    result = rig.confirm_and_call("block_card", {"product_id": people["alice"]["card_id"]}, alice)
    assert result.verification == "not_verified" and result.handoff_required


def test_transient_write_failures_are_retried_with_backoff(rig, people, purchases):
    alice = rig.login(people["alice"])
    rig.faults.set("case_store.write", "timeout", times=2)
    result = rig.confirm_and_call("open_dispute_case", _open_args(_small_purchase(purchases)["transaction_id"]),
                                  alice)
    assert result.ok and result.verification == "verified"
    assert rig.sleeps == [0.05, 0.1]
    assert rig.cases._con.execute("SELECT count(*) FROM sandbox.dispute_cases").fetchone()[0] == 1


@pytest.mark.parametrize(("op", "tool", "args"), [
    ("case_store.write", "open_dispute_case", None),
    ("warehouse.read", "list_recent_transactions", {}),
    ("warehouse.read", "get_customer_profile", {}),
])
def test_bounded_retries_then_safe_fallback(rig, people, purchases, op, tool, args):
    alice = rig.login(people["alice"])
    rig.faults.set(op, "error")
    if args is None:
        result = rig.confirm_and_call(tool, _open_args(_small_purchase(purchases)["transaction_id"]), alice)
    else:
        result = rig.call(tool, args, alice)
    assert not result.ok and result.error.code == "tool_unavailable"
    assert result.handoff_required and result.handoff_reason == "tool_failure"
    assert result.attempts == 3 and rig.sleeps == [0.05, 0.1]
    assert rig.faults.fired[op] == 3


# ---- contracts exported -------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(TOOLS))
def test_exported_tool_schema_is_up_to_date(name):
    path = OUT_DIR / f"{name}.json"
    assert path.exists(), "run: uv run python -m agent.tools.export_schemas"
    assert path.read_text(encoding="utf-8") == render(name)

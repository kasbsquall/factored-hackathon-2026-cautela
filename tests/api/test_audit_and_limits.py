"""The audit index and conversation ids over the API, the stored-chain status, demo logins restricted to the seed,
the console rate limit, and the proxy-forwarded client address. No network, no model."""

from __future__ import annotations

import json

import duckdb
import pytest

from api.app import client_address
from api.settings import ApiSettings, SettingsError
from tests.agent.conftest import warehouse  # noqa: F401 (fixture)
from tests.api.test_api import CONSOLE, _login, _not_recognized, client, make_client  # noqa: F401 (fixtures)

PROXY_KEY = "proxy-key-for-tests-0123456789abcdef"


def _resolved_conversation(client) -> tuple[dict[str, str], dict, dict]:
    """The normal scenario to the end: description, "I do not recognize it", confirm. Three turns."""
    auth, ident = _login(client, "normal")
    first = client.post("/conversations/turn", headers=auth, json={"message": ident["messages"]["es"][0]}).json()
    asked = _not_recognized(client, auth, first)
    done = client.post(f"/conversations/{asked['conversation_id']}/confirm", headers=auth,
                       json={"confirmation_id": asked["confirmation"]["confirmation_id"]}).json()
    assert done["stage"] == "resolved", done["stage"]
    return auth, first, done


# ---- conversation id and the audit index ----------------------------------------------------------------------
def test_every_record_of_every_turn_carries_the_conversation_id(client):
    _, first, done = _resolved_conversation(client)
    cid = done["conversation_id"]
    audit = client.get(f"/console/conversations/{cid}/audit", headers=CONSOLE).json()
    assert len(audit["trace_ids"]) == 3 and audit["trace_ids"][0] == first["trace_id"]
    assert {r["trace_id"] for r in audit["records"]} == set(audit["trace_ids"])
    assert all(r["conversation_id"] == cid for r in audit["records"])
    steps = {r["step"] for r in audit["records"]}
    assert {"orchestrator.understand", "orchestrator.decide.disposition", "orchestrator.verify", "verify"} <= steps
    assert "list_recent_transactions" in {r["tool"] for r in audit["records"]}, "the pool the ranking read"
    runtime = client.app.state.runtime
    by_trace = [r for r in runtime.stack.audit.records() if r.trace_id in set(audit["trace_ids"])]
    assert len(by_trace) == len(audit["records"]), "no record of those turns is missing the conversation id"


def test_the_index_lists_resolved_conversations_and_handoffs(client):
    _, _, done = _resolved_conversation(client)
    auth, ident = _login(client, "human")
    first = client.post("/conversations/turn", headers=auth, json={"message": ident["messages"]["es"][0]}).json()
    asked = _not_recognized(client, auth, first)
    handed = client.post(f"/conversations/{asked['conversation_id']}/confirm", headers=auth,
                         json={"confirmation_id": asked["confirmation"]["confirmation_id"]}).json()
    assert client.get("/console/conversations").status_code == 403
    index = client.get("/console/conversations", headers=CONSOLE).json()
    rows = {row["conversation_id"]: row for row in index}
    resolved, handoff = rows[done["conversation_id"]], rows[handed["conversation_id"]]
    assert resolved["stage"] == "resolved" and resolved["handoff_id"] is None and resolved["case_id"]
    assert resolved["turns"] == 3 and resolved["trace_ids"][-1] == done["trace_id"] and resolved["records"] > 0
    assert handoff["stage"] == "handed_off" and handoff["transfer_reason"] == "amount_above_threshold"
    assert index[0]["conversation_id"] == handed["conversation_id"], "newest first"


def test_a_turn_trace_names_its_conversation_and_every_turn_of_it(client):
    _, first, done = _resolved_conversation(client)
    trace = client.get(f"/console/traces/{done['trace_id']}", headers=CONSOLE).json()
    assert trace["conversation_id"] == done["conversation_id"]
    assert trace["conversation_trace_ids"][0] == first["trace_id"]
    assert trace["conversation_trace_ids"][-1] == done["trace_id"] and len(trace["conversation_trace_ids"]) == 3
    assert all(r["trace_id"] == done["trace_id"] for r in trace["records"])


# ---- chain status -------------------------------------------------------------------------------------------
def test_chain_status_reads_the_stored_files_and_reports_an_edit(make_client, tmp_path):
    audit_dir = tmp_path / "audit"
    client, _ = make_client(audit_dir=audit_dir)
    _, _, done = _resolved_conversation(client)
    chain = client.get(f"/console/traces/{done['trace_id']}", headers=CONSOLE).json()["chain"]
    assert chain["status"] == "intact" and chain["source"] == "stored_files" and chain["records_checked"] > 0
    path = next(audit_dir.glob("audit-*.jsonl"))
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[3])
    record["outcome"] = "edited"
    lines[3] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    broken = client.get(f"/console/traces/{done['trace_id']}", headers=CONSOLE).json()["chain"]
    assert broken["status"] == "broken" and broken["first_bad_seq"] == record["seq"]


def test_without_an_audit_directory_the_status_says_it_checked_memory(client):
    _, _, done = _resolved_conversation(client)
    chain = client.get(f"/console/traces/{done['trace_id']}", headers=CONSOLE).json()["chain"]
    assert chain["status"] == "intact" and chain["source"] == "memory"


def test_the_service_start_purges_expired_audit_files(make_client, tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    expired = audit_dir / "audit-2000-01-01.jsonl"
    expired.write_text("{}\n", encoding="utf-8")
    make_client(audit_dir=audit_dir)
    assert not expired.exists()


# ---- demo logins ---------------------------------------------------------------------------------------------
def _non_seed_active_document(warehouse, seeded: set[str]) -> str:
    with duckdb.connect(str(warehouse), read_only=True) as con:
        rows = con.execute("SELECT document_number FROM silver.customers WHERE customer_status = 'Active' "
                           "ORDER BY customer_id").fetchall()
    return next(doc for (doc,) in rows if doc not in seeded)


def test_demo_outbox_shows_codes_of_seeded_identities_only(make_client, warehouse):
    client, runtime = make_client()
    seeded = {i["document_number"] for i in client.get("/demo/identities").json()}
    other = _non_seed_active_document(warehouse, seeded)
    before = len(runtime.stack.channel.outbox)
    challenge = client.post("/auth/challenge", json={"document_number": other}).json()
    assert len(runtime.stack.channel.outbox) == before + 1, "the customer's own channel still gets the code"
    assert client.get(f"/demo/outbox/{challenge['challenge_id']}").status_code == 404
    _login(client, "normal")  # a seeded identity still logs in from the outbox


def test_demo_mode_is_off_unless_the_environment_turns_it_on():
    assert ApiSettings().demo_mode is False
    assert ApiSettings.from_env({}).demo_mode is False
    assert ApiSettings.from_env({"CAUTELA_DEMO_MODE": ""}).demo_mode is False
    assert ApiSettings.from_env({"CAUTELA_DEMO_MODE": "1"}).demo_mode is True
    assert ApiSettings.from_env({"CAUTELA_DEMO_MODE": "true"}).demo_mode is True


# ---- console rate limit ---------------------------------------------------------------------------------------
def test_console_routes_are_rate_limited(make_client):
    client, _ = make_client(console_rate=(2, 60))
    codes = [client.get("/console/conversations", headers=CONSOLE).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert client.get("/console/handoffs", headers=CONSOLE).status_code == 429


# ---- client address -----------------------------------------------------------------------------------------
class _Req:
    def __init__(self, peer: str, headers: dict[str, str]) -> None:
        self.client = type("C", (), {"host": peer})()
        self.headers = {k.lower(): v for k, v in headers.items()}


@pytest.mark.parametrize(("headers", "expected"), [
    ({}, "10.0.0.1"),
    ({"X-Cautela-Client": "203.0.113.7"}, "10.0.0.1"),  # no proxy key: the claim is ignored
    ({"X-Cautela-Client": "203.0.113.7", "X-Cautela-Proxy-Key": "wrong-key-wrong-key-wrong-key-00"}, "10.0.0.1"),
    ({"X-Cautela-Client": "203.0.113.7", "X-Cautela-Proxy-Key": PROXY_KEY}, "203.0.113.7"),
    ({"X-Cautela-Client": "not-an-address", "X-Cautela-Proxy-Key": PROXY_KEY}, "10.0.0.1"),
    ({"X-Forwarded-For": "203.0.113.7", "X-Cautela-Proxy-Key": PROXY_KEY}, "10.0.0.1"),
])
def test_forwarded_client_is_trusted_only_with_the_proxy_key(headers, expected):
    assert client_address(_Req("10.0.0.1", headers), PROXY_KEY) == expected


def test_without_a_configured_proxy_key_nothing_forwarded_is_read():
    request = _Req("10.0.0.1", {"X-Cautela-Client": "203.0.113.7", "X-Cautela-Proxy-Key": PROXY_KEY})
    assert client_address(request, None) == "10.0.0.1"


def test_visitors_behind_the_proxy_get_their_own_buckets(make_client):
    client, _ = make_client(turn_rate=(1, 60), proxy_key=PROXY_KEY)

    def turn(visitor: str, key: str = PROXY_KEY) -> int:
        headers = {"X-Cautela-Client": visitor, "X-Cautela-Proxy-Key": key}
        return client.post("/conversations/turn", json={"message": "x"}, headers=headers).status_code

    assert [turn("203.0.113.7"), turn("203.0.113.7"), turn("198.51.100.9")] == [401, 429, 401]
    assert turn("192.0.2.44", key="wrong-key-wrong-key-wrong-key-00") == 401  # the socket bucket
    assert turn("192.0.2.45", key="wrong-key-wrong-key-wrong-key-00") == 429, "a made-up address is no new bucket"


def test_a_short_proxy_key_is_refused():
    with pytest.raises(SettingsError):
        ApiSettings.from_env({"CAUTELA_PROXY_KEY": "short"})

"""API tests with TestClient over the synthetic fixture warehouse. No network, no real secret, no model."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent.clock import FrozenClock
from agent.handoff import validate_handoff
from agent.orchestrator import RuleDisposition
from agent.orchestrator.llm_setup import LLMChoice
from api import export_openapi, seed
from api.app import create_app
from api.runtime import Runtime
from api.settings import ApiSettings, SettingsError
from tests.agent.conftest import NOW, warehouse  # noqa: F401 (fixture)

CONSOLE = {"X-Console-Key": "test-console-key-0123456789"}


@pytest.fixture()
def make_client(warehouse, tmp_path):
    made = []

    def build(**overrides) -> tuple[TestClient, Runtime]:
        seed_file = tmp_path / "seed.json"
        seed_file.write_text(json.dumps(seed.build_seed(warehouse, NOW)), encoding="utf-8")
        settings = ApiSettings(warehouse=warehouse, seed_file=seed_file, console_key=CONSOLE["X-Console-Key"],
                               **{"audit_dir": None, "demo_mode": True, **overrides})
        runtime = Runtime(settings, environ={}, clock=FrozenClock(NOW),
                          llm_choice=LLMChoice(None, "fake", None, "test"), disposition=RuleDisposition())
        client = TestClient(create_app(runtime), raise_server_exceptions=False)
        made.append((client, runtime))
        return client, runtime
    yield build
    for client, runtime in made:
        client.close()
        runtime.close()


@pytest.fixture()
def client(make_client) -> TestClient:
    return make_client()[0]


def _identity(client, scenario: str) -> dict:
    return next(i for i in client.get("/demo/identities").json() if i["scenario"] == scenario)


def _login(client, scenario: str) -> tuple[dict[str, str], dict]:
    ident = _identity(client, scenario)
    challenge = client.post("/auth/challenge", json={"document_number": ident["document_number"]}).json()
    code = client.get(f"/demo/outbox/{challenge['challenge_id']}").json()["code"]
    session = client.post("/auth/verify", json={"challenge_id": challenge["challenge_id"], "code": code})
    assert session.status_code == 200
    return {"Authorization": f"Bearer {session.json()['session_token']}"}, ident


def _not_recognized(client, auth: dict[str, str], body: dict) -> dict:
    """Answer "I do not recognize it", which is what issues the dispute confirmation."""
    assert body["stage"] == "awaiting_recognition" and body["confirmation"] is None, body["stage"]
    answer = client.post(f"/conversations/{body['conversation_id']}/recognize", headers=auth,
                         json={"recognition_id": body["recognition"]["recognition_id"], "recognized": False})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_health_reports_components_without_internals(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["llm_provider"] == "fake"
    assert body["disposition_model"] == "rules_fixed_baseline"
    assert body["disposition_source"] == "rules baseline, chosen explicitly"
    text = json.dumps(body)
    assert "duckdb" not in text and "\\" not in text and "/data" not in text


def test_normal_flow_end_to_end_in_both_languages(client):
    for lang in ("es", "pt"):
        auth, ident = _login(client, "normal")
        first = client.post("/conversations/turn", headers=auth,
                            json={"message": ident["messages"][lang][0], "language": lang})
        assert first.status_code == 200, first.text
        body = _not_recognized(client, auth, first.json())
        assert body["stage"] == "awaiting_confirmation" and body["language"] == lang
        assert "token" not in json.dumps(body["confirmation"])
        done = client.post(f"/conversations/{body['conversation_id']}/confirm", headers=auth,
                           json={"confirmation_id": body["confirmation"]["confirmation_id"]}).json()
        assert done["stage"] == "resolved" and done["case"]["verified"] is True
        case = client.get(f"/cases/{done['case']['case_id']}", headers=auth).json()
        assert case["status"] == "open"
        view = client.get(f"/conversations/{body['conversation_id']}", headers=auth).json()
        assert [line["role"] for line in view["transcript"]] == ["customer", "assistant", "assistant", "assistant"]
        client.post("/auth/logout", headers=auth)


def test_human_case_reaches_the_console_queue_with_audit(client):
    auth, ident = _login(client, "human")
    first = client.post("/conversations/turn", headers=auth, json={"message": ident["messages"]["es"][0]}).json()
    first = _not_recognized(client, auth, first)
    done = client.post(f"/conversations/{first['conversation_id']}/confirm", headers=auth,
                       json={"confirmation_id": first["confirmation"]["confirmation_id"]}).json()
    assert done["stage"] == "handed_off" and done["transfer_reason"] == "amount_above_threshold"
    assert client.get("/console/handoffs").status_code == 403
    assert client.get("/console/handoffs", headers={"X-Console-Key": "wrong"}).status_code == 403
    queue = client.get("/console/handoffs", headers=CONSOLE).json()
    assert queue[0]["handoff"]["handoff_id"] == done["handoff_id"]
    item = client.get(f"/console/handoffs/{done['handoff_id']}", headers=CONSOLE).json()
    validate_handoff({k: v for k, v in item["handoff"].items()})
    audit = client.get(f"/console/conversations/{first['conversation_id']}/audit", headers=CONSOLE).json()
    assert audit["chain"]["status"] == "intact" and len(audit["trace_ids"]) == 3
    assert {"orchestrator.escalate", "orchestrator.verify", "guard", "tool"} <= {r["step"] for r in audit["records"]}
    trace = client.get(f"/console/traces/{done['trace_id']}", headers=CONSOLE).json()
    assert all(r["trace_id"] == done["trace_id"] for r in trace["records"])


def test_confirmation_token_never_leaves_the_server(client):
    runtime = client.app.state.runtime
    auth, ident = _login(client, "normal")
    asked = client.post("/conversations/turn", headers=auth, json={"message": ident["messages"]["es"][0]})
    first = client.post(f"/conversations/{asked.json()['conversation_id']}/recognize", headers=auth,
                        json={"recognition_id": asked.json()["recognition"]["recognition_id"], "recognized": False})
    state = runtime.orchestrator.store.get_any(first.json()["conversation_id"])
    secret = state.pending.token
    view = client.get(f"/conversations/{state.conversation_id}", headers=auth)
    audit = client.get(f"/console/conversations/{state.conversation_id}/audit", headers=CONSOLE)
    for response in (first, view, audit):
        assert secret not in response.text


def test_auth_errors_are_typed_and_do_not_echo_input(client):
    bad = client.post("/auth/challenge", json={"document_number": "<script>1234</script>"})
    assert bad.status_code == 422 and bad.json()["error"]["fields"] == ["body.document_number"]
    assert "script" not in bad.text
    unknown = client.post("/auth/challenge", json={"document_number": "99999999"})
    assert unknown.status_code == 200, "an unknown document gets the same answer shape"
    assert client.get(f"/demo/outbox/{unknown.json()['challenge_id']}").status_code == 404
    wrong = client.post("/auth/verify", json={"challenge_id": unknown.json()["challenge_id"], "code": "123456"})
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "otp_invalid"
    assert client.post("/conversations/turn", json={"message": "hola"}).json()["error"]["code"] == "session_invalid"
    forged = client.post("/conversations/turn", headers={"Authorization": "Bearer abc.def"}, json={"message": "x"})
    assert forged.status_code == 401 and forged.json()["error"]["code"] == "session_invalid"
    extra = client.post("/conversations/turn", headers={"Authorization": "Bearer abc.def"},
                        json={"message": "x", "customer_id": "C000001"})
    assert extra.status_code == 422, "unknown fields such as customer_id are rejected"


def test_expired_and_revoked_sessions(client):
    auth, ident = _login(client, "normal")
    client.post("/auth/logout", headers=auth)
    revoked = client.post("/conversations/turn", headers=auth, json={"message": "hola"})
    assert revoked.status_code == 401 and revoked.json()["error"]["code"] == "session_revoked"
    auth, _ = _login(client, "normal")
    client.app.state.runtime.clock.advance(minutes=16)
    expired = client.post("/conversations/turn", headers=auth, json={"message": "hola"})
    assert expired.status_code == 401 and expired.json()["error"]["code"] == "session_expired"


def test_other_customers_cannot_read_a_conversation_or_case(client):
    auth_a, ident = _login(client, "normal")
    first = client.post("/conversations/turn", headers=auth_a, json={"message": ident["messages"]["es"][0]}).json()
    first = _not_recognized(client, auth_a, first)
    done = client.post(f"/conversations/{first['conversation_id']}/confirm", headers=auth_a,
                       json={"confirmation_id": first["confirmation"]["confirmation_id"]}).json()
    auth_b, _ = _login(client, "human")
    assert client.get(f"/conversations/{first['conversation_id']}", headers=auth_b).status_code == 404
    other_case = client.get(f"/cases/{done['case']['case_id']}", headers=auth_b)
    assert other_case.status_code == 404 and other_case.json()["error"]["code"] == "not_found"
    assert client.post("/conversations/turn", headers=auth_b,
                       json={"message": "hola", "conversation_id": first["conversation_id"]}).status_code == 404


def test_auth_endpoints_are_rate_limited(make_client):
    client, _ = make_client(auth_rate=(3, 60))
    codes = [client.post("/auth/challenge", json={"document_number": "12345678"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert client.post("/auth/challenge", json={"document_number": "12345678"}).json()["error"]["code"] == \
        "rate_limited"


def test_turn_endpoint_is_rate_limited(make_client):
    client, _ = make_client(turn_rate=(2, 60))
    codes = [client.post("/conversations/turn", json={"message": "x"}).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


def test_cors_allows_only_configured_origins(make_client):
    client, _ = make_client(cors_origins=("http://localhost:3000",))
    ok = client.options("/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_unexpected_errors_do_not_leak_internals(client, monkeypatch):
    auth, _ = _login(client, "normal")

    def boom(*args, **kwargs):
        raise RuntimeError("SELECT * FROM silver.customers at C:\\secret\\path")
    monkeypatch.setattr(client.app.state.runtime.orchestrator, "turn", boom)
    response = client.post("/conversations/turn", headers=auth, json={"message": "hola"})
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Internal error.", "trace_id": None,
                                         "fields": []}}


def test_security_headers_and_demo_mode_off(make_client):
    client, _ = make_client(demo_mode=False)
    response = client.get("/demo/identities")
    assert response.status_code == 404
    assert response.headers["x-content-type-options"] == "nosniff" and response.headers["cache-control"] == "no-store"
    assert client.post("/auth/challenge", json={"document_number": "12345678"}).status_code == 200


def test_openapi_file_matches_the_code():
    committed = export_openapi.OUT.read_text(encoding="utf-8")
    assert committed == export_openapi.render(), "run `uv run python -m api.export_openapi`"


def test_committed_seed_matches_the_fixture(warehouse):
    committed = json.loads(seed.DEFAULT_OUT.read_text(encoding="utf-8"))
    assert committed["identities"] == seed.build_seed(warehouse)["identities"], "run `uv run python -m api.seed`"
    assert {i["scenario"] for i in committed["identities"]} >= {"normal", "human", "ambiguous"}


def test_seed_refuses_organizer_data_inside_api(tmp_path):
    with pytest.raises(SystemExit):
        seed.main(["--warehouse", str(tmp_path / "other.duckdb"), "--out", str(seed.DEFAULT_OUT)])


def test_settings_parse_and_validate():
    s = ApiSettings.from_env({"CAUTELA_CORS_ORIGINS": "http://a.test, http://b.test", "CAUTELA_RATE_AUTH": "5/30",
                              "CAUTELA_DEMO_MODE": "0", "CAUTELA_AS_OF": "2026-06-01T12:00:00"})
    assert s.cors_origins == ("http://a.test", "http://b.test") and s.auth_rate == (5, 30)
    assert s.demo_mode is False and s.as_of.tzinfo is not None and s.host == "127.0.0.1"
    with pytest.raises(SettingsError):
        ApiSettings.from_env({"CAUTELA_RATE_TURN": "lots"})

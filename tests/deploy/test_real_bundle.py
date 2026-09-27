"""The organizer-derived demo bundle, when it exists on this machine (deploy/demo-bundle/, git-ignored): it matches the
committed lock, and every identity plays its scenario through the HTTP API in its first language with the learned
model and no language model. Skips in CI and wherever the bundle was not built (`make demo-artifacts`)."""

from __future__ import annotations

import json

import duckdb
import pytest
from fastapi.testclient import TestClient

from api.settings import ROOT, ApiSettings
from deploy import lock, serve

BUNDLE = ROOT / "deploy" / "demo-bundle"
LOCK = ROOT / "deploy" / "demo-bundle.lock.json"
pytestmark = pytest.mark.skipif(not BUNDLE.is_dir(), reason="demo bundle not built (organizer data)")


@pytest.fixture(scope="module")
def client():
    env = {"CAUTELA_BUNDLE_DIR": str(BUNDLE), "CAUTELA_BUNDLE_LOCK": str(LOCK), "LLM_PROVIDER": "fake"}
    settings = ApiSettings(audit_dir=None, console_key="test-console-key-0123456789", auth_rate=(100, 60),
                           turn_rate=(100, 60), demo_mode=True)
    app = serve.build_app(settings, environ=env)
    with TestClient(app) as c:
        yield c


def test_the_bundle_matches_the_committed_lock():
    data = lock.verify(BUNDLE, LOCK)
    assert data["disposition_model"].startswith("learned_ranker_disposition")
    text = LOCK.read_text(encoding="utf-8")
    seed = json.loads((BUNDLE / lock.SEED).read_text(encoding="utf-8"))
    for ident in seed["identities"]:
        assert ident["customer_id"] not in text and ident["document_number"] not in text


def test_health_reports_the_learned_model_and_the_verified_bundle(client):
    body = client.get("/health").json()
    locked = json.loads(LOCK.read_text(encoding="utf-8"))
    assert body["disposition_model"] == locked["disposition_model"]
    assert body["demo_bundle"]["verified"] is True and body["demo_bundle"]["as_of"] == locked["as_of"]
    assert body["llm_provider"] == "fake"


SEED = json.loads((BUNDLE / lock.SEED).read_text(encoding="utf-8")) if BUNDLE.is_dir() else {"identities": []}


@pytest.mark.parametrize("ident", SEED["identities"], ids=lambda i: i["scenario"])
def test_each_identity_plays_its_scenario_over_http(client, ident):
    lang = ident["first_language"]
    listed = {i["scenario"]: i for i in client.get("/demo/identities").json()}
    assert set(listed[ident["scenario"]]) == {"document_number", "label", "scenario", "messages"}
    challenge = client.post("/auth/challenge", json={"document_number": ident["document_number"]}).json()
    code = client.get(f"/demo/outbox/{challenge['challenge_id']}").json()["code"]
    token = client.post("/auth/verify", json={"challenge_id": challenge["challenge_id"], "code": code}).json()
    auth = {"Authorization": f"Bearer {token['session_token']}"}
    stages, body = [], {}
    messages = ident["messages"][lang][:2] if ident["scenario"] == "bad_data" else ident["messages"][lang][:1]
    for text in messages:
        body = client.post("/conversations/turn", headers=auth,
                           json={"message": text, "language": lang, "conversation_id": body.get("conversation_id")}
                           ).json()
        stages.append(body["stage"])
    if body["stage"] == "clarifying" and ident["scenario"] == "ambiguous":
        with duckdb.connect(str(BUNDLE / lock.WAREHOUSE), read_only=True) as con:
            when, amount = con.execute("SELECT transaction_date, amount FROM gold.customer_transactions "
                                       "WHERE transaction_id = ?", [ident["transaction_id"]]).fetchone()
        pick = next(o["index"] for o in body["options"] if o["charge"]["amount"] == float(amount)
                    and o["charge"]["transaction_date"][:10] == when.date().isoformat())
        body = client.post("/conversations/turn", headers=auth,
                           json={"message": f"{'La' if lang == 'es' else 'A'} {pick}",
                                 "conversation_id": body["conversation_id"]}).json()
        stages.append(body["stage"])
    if body.get("recognition"):
        body = client.post(f"/conversations/{body['conversation_id']}/recognize", headers=auth,
                           json={"recognition_id": body["recognition"]["recognition_id"], "recognized": False}).json()
        stages.append(body["stage"])
    if body.get("confirmation"):
        body = client.post(f"/conversations/{body['conversation_id']}/confirm", headers=auth,
                           json={"confirmation_id": body["confirmation"]["confirmation_id"]}).json()
        stages.append(body["stage"])
    assert stages == ident["validated"][lang]["stages"], body.get("reply")

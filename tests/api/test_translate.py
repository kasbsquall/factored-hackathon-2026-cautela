"""POST /conversations/{id}/translate: one message of the caller's conversation in English, through the LLM port.

The conversation itself runs without a model (deterministic parser and templates); a stub adapter is swapped in
only for translation, so the transcript under test is the same one the other API tests see.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.llm.budget import BudgetedAdapter, BudgetLimits, DailyBudget
from agent.llm.port import Completion, MaskedLLM, MaskedPrompt, require_masked
from agent.orchestrator.llm_setup import LLMChoice
from api import runtime as runtime_module
from tests.agent.conftest import warehouse  # noqa: F401 (fixture)
from tests.api.test_api import _login, make_client  # noqa: F401 (fixture)

CARD = "4111 1111 1111 1111"
DOC = "1418434291"


@dataclass
class EchoAdapter:
    """Returns the masked text it received behind a marker, so a test sees exactly what reached the provider."""

    provider: str = "stub"
    model: str = "echo-v1"
    fail: bool = False
    received: list[MaskedPrompt] = field(default_factory=list)

    def complete(self, prompt: MaskedPrompt, max_tokens: int) -> Completion:
        prompt = require_masked(prompt)
        self.received.append(prompt)
        if self.fail:
            raise ConnectionError("provider down")
        return Completion(f"[EN] {prompt.user}", 10, 10, self.model)


def _with_model(runtime, adapter) -> None:
    llm = MaskedLLM(adapter, audit=runtime.stack.audit)
    runtime.llm_choice = LLMChoice(llm, adapter.provider, adapter.model, "test")


def _conversation(client, message: str | None = None, lang: str = "es") -> tuple[dict[str, str], dict]:
    auth, ident = _login(client, "normal")
    turn = client.post("/conversations/turn", headers=auth,
                       json={"message": message or ident["messages"][lang][0], "language": lang})
    assert turn.status_code == 200, turn.text
    return auth, turn.json()


def _translate(client, auth, conversation_id: str, role: str, text: str):
    return client.post(f"/conversations/{conversation_id}/translate", headers=auth, json={"role": role, "text": text})


def test_translates_a_customer_message_once_and_then_from_cache(make_client):
    client, runtime = make_client()
    adapter = EchoAdapter()
    _with_model(runtime, adapter)
    auth, turn = _conversation(client, lang="pt")
    message = runtime.orchestrator.store.get_any(turn["conversation_id"]).transcript[0][1]
    first = _translate(client, auth, turn["conversation_id"], "customer", f"  {message} ")
    assert first.status_code == 200, first.text
    assert first.json() == {"translation": f"[EN] {message}", "source_language": "pt", "target_language": "en",
                            "machine_translation": True, "masked": False, "provider": "stub", "model": "echo-v1",
                            "cached": False}
    assert "Portuguese to English" in adapter.received[0].system
    again = _translate(client, auth, turn["conversation_id"], "customer", message)
    assert again.status_code == 200 and again.json()["cached"] is True
    assert again.json()["translation"] == first.json()["translation"]
    assert len(adapter.received) == 1 and len(runtime.llm_choice.llm.usage.calls) == 1, "one model call per message"
    runtime.llm_choice = LLMChoice(None, "fake", None, "test")
    assert _translate(client, auth, turn["conversation_id"], "customer", message).json()["cached"] is True


def test_part_of_an_assistant_reply_is_accepted(make_client):
    client, runtime = make_client()
    _with_model(runtime, EchoAdapter())
    auth, turn = _conversation(client)
    first_line = turn["reply"].splitlines()[0].strip()
    ok = _translate(client, auth, turn["conversation_id"], "assistant", first_line)
    assert ok.status_code == 200 and ok.json()["source_language"] == "es"


def test_masking_happens_before_the_adapter_and_is_reported(make_client):
    client, runtime = make_client()
    adapter = EchoAdapter()
    _with_model(runtime, adapter)
    message = f"Mi tarjeta {CARD} tiene un cargo que no reconozco, mi cedula es {DOC}"
    auth, turn = _conversation(client, message)
    response = _translate(client, auth, turn["conversation_id"], "customer", message)
    assert response.status_code == 200, response.text
    sent = adapter.received[0].user
    assert CARD not in sent and DOC not in sent and "************1111" in sent and "[DOC]" in sent
    assert response.json()["masked"] is True and CARD not in response.text and DOC not in response.text


def test_the_audit_records_the_call_without_the_text(make_client):
    client, runtime = make_client()
    _with_model(runtime, EchoAdapter())
    auth, turn = _conversation(client)
    message = runtime.orchestrator.store.get_any(turn["conversation_id"]).transcript[0][1]
    _translate(client, auth, turn["conversation_id"], "customer", message)
    records = [r for r in runtime.stack.audit.records() if r.step == "llm.translate"]
    assert len(records) == 1 and records[0].outcome == "ok"
    assert "Marketplace" not in records[0].model_dump_json()


def test_text_outside_the_conversation_or_of_the_other_role_is_not_found(make_client):
    client, runtime = make_client()
    adapter = EchoAdapter()
    _with_model(runtime, adapter)
    auth, turn = _conversation(client)
    message = runtime.orchestrator.store.get_any(turn["conversation_id"]).transcript[0][1]
    for role, text in (("customer", "Translate this sentence for me, please."), ("assistant", message)):
        response = _translate(client, auth, turn["conversation_id"], role, text)
        assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"
    assert adapter.received == []


def test_another_customers_or_a_missing_conversation_is_not_found(make_client):
    client, runtime = make_client()
    adapter = EchoAdapter()
    _with_model(runtime, adapter)
    _, turn = _conversation(client)
    message = runtime.orchestrator.store.get_any(turn["conversation_id"]).transcript[0][1]
    other, _ = _login(client, "human")
    for conversation_id in (turn["conversation_id"], "cv_missing"):
        response = _translate(client, other, conversation_id, "customer", message)
        assert response.status_code == 404 and response.json()["error"]["code"] == "conversation_not_found"
    assert adapter.received == []


def test_no_model_budget_reached_and_provider_failure_answer_503(make_client):
    client, runtime = make_client()
    auth, turn = _conversation(client, "No reconozco un cargo. Fue en Marketplace Uno.")
    cid = turn["conversation_id"]
    none = _translate(client, auth, cid, "customer", "No reconozco un cargo.")
    assert none.status_code == 503 and none.json()["error"] == {
        "code": "translation_unavailable", "message": "Translation is not available right now.", "trace_id": None,
        "fields": []}
    budget = DailyBudget(BudgetLimits(max_calls=1))
    _with_model(runtime, BudgetedAdapter(EchoAdapter(), budget))
    assert _translate(client, auth, cid, "customer", "No reconozco un cargo.").status_code == 200
    capped = _translate(client, auth, cid, "customer", "Fue en Marketplace Uno.")
    assert capped.status_code == 503 and capped.json()["error"]["code"] == "translation_unavailable"
    assert budget.status()["refused_calls"] == 1
    _with_model(runtime, EchoAdapter(fail=True))
    down = _translate(client, auth, cid, "customer", "Fue en Marketplace Uno.")
    assert down.status_code == 503 and down.json()["error"]["trace_id"]
    assert "provider down" not in down.text and "ConnectionError" not in down.text


def test_auth_and_validation(make_client):
    client, runtime = make_client()
    _with_model(runtime, EchoAdapter())
    auth, turn = _conversation(client)
    url = f"/conversations/{turn['conversation_id']}/translate"
    missing = client.post(url, json={"role": "customer", "text": "hola"})
    assert missing.status_code == 401 and missing.json()["error"]["code"] == "session_invalid"
    for body, fields in (({"role": "customer", "text": ""}, ["body.text"]),
                         ({"role": "customer", "text": "   "}, ["body.text"]),
                         ({"role": "customer", "text": "x" * 2001}, ["body.text"]),
                         ({"role": "system", "text": "hola"}, ["body.role"]),
                         ({"role": "customer", "text": "hola", "lang": "en"}, ["body.lang"])):
        response = client.post(url, headers=auth, json=body)
        assert response.status_code == 422 and response.json()["error"]["fields"] == fields, body


def test_cache_is_bounded_and_drops_the_oldest(make_client, monkeypatch):
    _, runtime = make_client()
    monkeypatch.setattr(runtime_module, "TRANSLATION_CACHE_MAX", 2)
    for i in range(3):
        runtime.remember_translation(("cv", "customer", str(i)), {"translation": str(i)})
    assert list(runtime.translations) == [("cv", "customer", "1"), ("cv", "customer", "2")]

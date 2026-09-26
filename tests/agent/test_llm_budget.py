"""Daily LLM budget: call and USD caps, fallback signal, day rollover, persistence, config. No network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agent.llm.adapters import FakeAdapter
from agent.llm.budget import (
    BudgetConfigError,
    BudgetedAdapter,
    BudgetExceeded,
    BudgetLimits,
    DailyBudget,
    budget_status,
    with_budget,
)
from agent.llm.port import Completion, LLMUnavailable, MaskedLLM, require_masked
from agent.llm.pricing import PriceEntry, PriceTable
from api import seed
from api.settings import ApiSettings
from deploy.serve import build_app
from tests.agent.conftest import NOW, warehouse  # noqa: F401 (fixture)

SCHEMA = {"type": "object", "properties": {"intent": {"type": "string"}}, "required": ["intent"]}
# 1000 input and 1000 output tokens at USD 100 per million each: USD 0.20 per call.
PRICES = PriceTable([PriceEntry("test", "m1", 100.0, 100.0, "test fixture")])


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class PricedAdapter:
    provider, model = "test", "m1"

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls = 0
        self.fail = fail

    def complete(self, prompt, max_tokens: int) -> Completion:
        require_masked(prompt)
        self.calls += 1
        if self.fail:
            raise self.fail
        return Completion('{"intent": "dispute"}', 1000, 1000, "m1")


def _budget(clock: Clock | None = None, **limits) -> DailyBudget:
    return DailyBudget(BudgetLimits(**limits), clock or Clock(datetime(2026, 9, 26, 10, tzinfo=UTC)))


def test_calls_under_the_cap_reach_the_provider_and_are_counted():
    inner = PricedAdapter()
    llm = MaskedLLM(BudgetedAdapter(inner, _budget(max_calls=5), PRICES), PRICES)
    assert llm.extract("no reconozco un cargo", SCHEMA) == {"intent": "dispute"}
    status = budget_status(llm)
    assert inner.calls == 1
    assert status["calls"] == 1 and status["usd_estimated"] == pytest.approx(0.2)
    assert status["mode"] == "llm" and status["exhausted_reason"] is None


def test_call_cap_refuses_without_contacting_the_provider():
    inner = FakeAdapter(extract_output={"intent": "dispute"})
    llm = MaskedLLM(BudgetedAdapter(inner, _budget(max_calls=2)), PriceTable.load())
    llm.extract("uno", SCHEMA)
    llm.reply({"a": 1}, "es")
    with pytest.raises(LLMUnavailable):  # the orchestrator's existing fallback path
        llm.extract("tres", SCHEMA)
    assert len(inner.received) == 2
    status = budget_status(llm)
    assert status["mode"] == "deterministic_fallback" and status["exhausted_reason"] == "max_calls"
    assert status["refused_calls"] == 1 and status["calls"] == 2
    assert llm.usage.calls[-1].error == "BudgetExceeded"


def test_usd_cap_allows_at_most_one_call_of_overshoot():
    inner = PricedAdapter()
    adapter = BudgetedAdapter(inner, _budget(max_usd=0.5), PRICES)
    llm = MaskedLLM(adapter, PRICES)
    for _ in range(3):  # 0.2, 0.4 (< 0.5, so the third call may start), 0.6
        llm.extract("x", SCHEMA)
    with pytest.raises(LLMUnavailable):
        llm.extract("x", SCHEMA)
    assert inner.calls == 3
    assert adapter.budget.status()["usd_estimated"] == pytest.approx(0.6)
    assert adapter.budget.status()["exhausted_reason"] == "max_usd"


def test_unknown_price_counts_the_call_but_adds_no_usd():
    llm = MaskedLLM(BudgetedAdapter(PricedAdapter(), _budget(), PriceTable([])), PriceTable([]))
    llm.extract("x", SCHEMA)
    status = budget_status(llm)
    assert status["calls"] == 1 and status["unpriced_calls"] == 1 and status["usd_estimated"] == 0


def test_provider_failure_is_counted_as_a_call_with_unknown_cost():
    inner = PricedAdapter(fail=TimeoutError("slow"))
    adapter = BudgetedAdapter(inner, _budget(), PRICES)
    with pytest.raises(LLMUnavailable):
        MaskedLLM(adapter, PRICES).extract("x", SCHEMA)
    assert adapter.budget.status()["calls"] == 1 and adapter.budget.status()["unpriced_calls"] == 1


def test_counters_reset_at_utc_midnight():
    clock = Clock(datetime(2026, 9, 26, 23, 59, tzinfo=UTC))
    budget = _budget(clock, max_calls=1)
    budget.reserve()
    with pytest.raises(BudgetExceeded):
        budget.reserve()
    assert budget.status()["resets_at"] == "2026-09-27T00:00:00+00:00"
    clock.now += timedelta(minutes=2)
    budget.reserve()
    assert budget.status()["day"] == "2026-09-27" and budget.status()["calls"] == 1


def test_state_file_survives_a_restart_and_is_ignored_on_another_day(tmp_path):
    path = tmp_path / "state" / "llm_budget.json"
    clock = Clock(datetime(2026, 9, 26, 10, tzinfo=UTC))
    first = _budget(clock, max_calls=3, state_file=path)
    first.reserve()
    first.charge(0.25)
    second = _budget(clock, max_calls=3, state_file=path)
    assert second.status()["calls"] == 1 and second.status()["usd_estimated"] == 0.25
    assert json.loads(path.read_text(encoding="utf-8"))["day"] == "2026-09-26"
    third = _budget(Clock(clock.now + timedelta(days=1)), max_calls=3, state_file=path)
    assert third.status()["calls"] == 0


def test_damaged_state_file_starts_from_zero(tmp_path):
    path = tmp_path / "llm_budget.json"
    path.write_text("{not json", encoding="utf-8")
    assert _budget(state_file=path).status()["calls"] == 0


def test_limits_from_env_defaults_and_validation(tmp_path):
    assert BudgetLimits.from_env({}) == BudgetLimits(2000, 1.0, None)
    env = {"LLM_DAILY_MAX_CALLS": "50", "LLM_DAILY_MAX_USD": "0.25", "LLM_BUDGET_FILE": str(tmp_path / "b.json")}
    assert BudgetLimits.from_env(env) == BudgetLimits(50, 0.25, tmp_path / "b.json")
    for name, value in [("LLM_DAILY_MAX_CALLS", "many"), ("LLM_DAILY_MAX_CALLS", "0"),
                        ("LLM_DAILY_MAX_USD", "-1"), ("LLM_DAILY_MAX_USD", "nan")]:
        with pytest.raises(BudgetConfigError, match=name):
            BudgetLimits.from_env({name: value})


def test_status_without_a_model_or_without_the_wrapper():
    assert budget_status(None) == {"enabled": False, "mode": "deterministic", "note": "no model configured"}
    assert budget_status(MaskedLLM(FakeAdapter(), PriceTable.load()))["enabled"] is False
    assert isinstance(with_budget(FakeAdapter(), {}), BudgetedAdapter)


def test_deployed_app_caps_the_model_and_reports_it_in_health(warehouse, tmp_path):
    """deploy/serve.py wiring: one real call allowed, the provider is unreachable, the service still answers."""
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps(seed.build_seed(warehouse, NOW)), encoding="utf-8")
    settings = ApiSettings(warehouse=warehouse, audit_dir=None, seed_file=seed_file,
                           console_key="test-console-key-0123456789")
    env = {"LLM_PROVIDER": "openai_compatible", "LLM_BASE_URL": "http://127.0.0.1:9/v1", "LLM_MODEL": "m",
           "LLM_API_KEY": "test-not-a-key", "LLM_TIMEOUT_S": "2", "LLM_DAILY_MAX_CALLS": "1"}
    app = build_app(settings, environ=env)
    with TestClient(app) as client:
        before = client.get("/health").json()
        assert before["llm_provider"] == "openai_compatible"
        assert before["llm_budget"]["mode"] == "llm" and before["llm_budget"]["max_calls"] == 1
        ident = client.get("/demo/identities").json()[0]
        challenge = client.post("/auth/challenge", json={"document_number": ident["document_number"]}).json()
        code = client.get(f"/demo/outbox/{challenge['challenge_id']}").json()["code"]
        token = client.post("/auth/verify", json={"challenge_id": challenge["challenge_id"], "code": code}).json()
        turn = client.post("/conversations/turn", headers={"Authorization": f"Bearer {token['session_token']}"},
                           json={"message": ident["messages"]["es"][0], "language": "es"})
        assert turn.status_code == 200 and turn.json()["reply_source"] == "template"
        after = client.get("/health").json()["llm_budget"]
        assert after["mode"] == "deterministic_fallback" and after["exhausted_reason"] == "max_calls"
        assert after["calls"] == 1 and after["refused_calls"] >= 1
        assert int(client.get("/health").headers["content-length"]) == len(client.get("/health").content)

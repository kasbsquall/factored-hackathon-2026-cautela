"""Fixtures for the orchestrator tests: the agent rig (synthetic fixture warehouse, frozen clock, random secret),
scenario cases picked from that warehouse, and a scripted LLM adapter that records every prompt it receives.

No test here touches the network. The rule baseline disposition is used by default so results do not depend on
the git-ignored learned artifacts; tests/orchestrator/test_disposition.py covers the learned model when present.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent.demo_scenarios import find_case
from agent.llm.port import Completion, MaskedLLM, MaskedPrompt, require_masked
from agent.llm.pricing import PriceTable
from agent.orchestrator import Orchestrator, RuleDisposition
from tests.agent.conftest import NOW, Rig, people, purchases, query, rig, warehouse  # noqa: F401 (fixtures)


@dataclass
class ScriptedAdapter:
    """Pops one scripted output per call: extraction outputs for JSON prompts, reply texts otherwise.
    An Exception in a queue is raised. Every prompt is recorded, so tests can inspect what a provider saw."""

    extracts: list[Any] = field(default_factory=list)
    replies: list[Any] = field(default_factory=list)
    provider: str = "fake"
    model: str = "fake-scripted"
    received: list[MaskedPrompt] = field(default_factory=list)

    def complete(self, prompt: MaskedPrompt, max_tokens: int) -> Completion:
        prompt = require_masked(prompt)
        self.received.append(prompt)
        queue = self.extracts if prompt.json_schema is not None else self.replies
        item = queue.pop(0) if queue else ({} if prompt.json_schema is not None else "")
        if isinstance(item, Exception):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        return Completion(text, 100, 20, self.model)

    def everything_sent(self) -> str:
        return "\n".join(p.system + "\n" + p.user for p in self.received)


def extraction(**values: Any) -> dict[str, Any]:
    base = {"intent": "dispute_charge", "topic": None, "amount": None, "currency": None, "date": None,
            "merchant": None, "selected_option": None, "record_ref": None}
    return {**base, **values}


@pytest.fixture(scope="session")
def cases(warehouse) -> dict:
    out = {}
    for scenario in ("normal", "human", "bad_data", "declined", "ambiguous"):
        case = find_case(warehouse, scenario, NOW, tuple(c.customer_id for c in out.values()))
        assert case is not None, f"the fixture has no {scenario} case"
        out[scenario] = case
    return out


@pytest.fixture()
def make_orchestrator(rig):
    def build(adapter: ScriptedAdapter | None = None, disposition=None, sink=None) -> Orchestrator:
        llm = MaskedLLM(adapter, PriceTable.load(), audit=rig.audit) if adapter is not None else None
        return Orchestrator(rig.service, llm, disposition or RuleDisposition(), handoff_sink=sink)
    return build


@pytest.fixture()
def orch(make_orchestrator) -> Orchestrator:
    return make_orchestrator()


@pytest.fixture()
def login(rig):
    def do(case_or_person) -> str:
        person = case_or_person if isinstance(case_or_person, dict) else {
            "document_number": case_or_person.document_number, "customer_id": case_or_person.customer_id}
        return rig.login(person)
    return do


def case_rows(rig: Rig, customer_id: str) -> int:
    return rig.cases._con.execute("SELECT count(*) FROM sandbox.dispute_cases WHERE customer_id = ?",
                                  [customer_id]).fetchone()[0]


def steps(result) -> list[str]:
    return [s.step for s in result.trail]


def not_recognized(orch: Orchestrator, token: str, result):
    """Answer the "do you recognize it?" step with "no", which is what issues the dispute confirmation."""
    assert result.stage == "awaiting_recognition" and result.recognition, (result.stage, result.reply)
    assert result.confirmation is None, "no confirmation exists before the customer answers"
    return orch.recognize(token, result.conversation_id, result.recognition["recognition_id"], recognized=False)

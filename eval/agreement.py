"""Agreement between the gold oracle (eval/oracle.py) and the service's policy engine on every gold charge.

The oracle re-implements rules.yaml on purpose; this check says how often the two agree on the charges the suite
uses, and lists the disagreements. It does not change any gold outcome.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import duckdb

from agent.policy.engine import PolicyInput, TransactionFacts, evaluate
from eval.oracle import gold_for_charge
from eval.paths import SLICE_PATH


def engine_view(facts: dict, as_of: datetime) -> tuple[str, str | None, bool]:
    decision = evaluate(PolicyInput(customer_country=facts.get("customer_country"), as_of=as_of.replace(tzinfo=UTC),
                                    transaction=TransactionFacts.model_validate(facts)))
    if decision.must_escalate:
        return "handoff", decision.primary_reason, decision.allows("open_dispute_case")
    if decision.allows("open_dispute_case"):
        return "resolved", None, True
    return "not_disputable", None, False


def agreement(suite: list[dict]) -> dict:
    with duckdb.connect(str(SLICE_PATH), read_only=True) as con:
        cols = [d[0] for d in con.execute("SELECT * FROM gold.dispute_policy_inputs LIMIT 0").description]
        facts = {r[0]: dict(zip(cols, r, strict=True)) for r in con.execute("SELECT * FROM gold.dispute_policy_inputs")
                 .fetchall()}
    seen, outcome, diffs = set(), Counter(), []
    for conv in suite:
        target = conv["gold"].get("target")
        key = (target, conv["clock_start"])
        if not target or key in seen:
            continue
        seen.add(key)
        as_of = datetime.fromisoformat(conv["clock_start"])
        g = gold_for_charge(facts[target], as_of)
        e = engine_view(facts[target], as_of)
        same = (g.kind, g.reason, g.expects_case) == e
        outcome["agree" if same else "disagree"] += 1
        if not same and len(diffs) < 20:
            diffs.append({"conv_id": conv["conv_id"], "oracle": [g.kind, g.reason, g.expects_case], "engine": list(e)})
    return {"charges": sum(outcome.values()), **outcome, "disagreements": diffs}

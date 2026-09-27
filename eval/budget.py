"""Hard USD cap for every paid model call made by this evaluation (end-to-end runs and the LLM ranker rung).

Reuses agent/llm/budget.py: a DailyBudget whose clock is pinned to one fixed day never rolls over, so its USD
counter is cumulative across processes through the ledger file data/eval/llm_spend.json (git-ignored). Every call
reserves before it starts and is charged after it ends with the list-price estimate from agent/llm/prices.yaml;
once the counter reaches the cap, the next call raises BudgetExceeded without contacting the provider. The last
call can overshoot by the cost of one call (a fraction of a cent here). Run one paid process at a time: two
processes writing the ledger would overwrite each other's counters (documented limit of DailyBudget).

The key is read from the git-ignored .env and never printed. CAUTELA_ENV_FILE points the same loader at the .env of
another checkout (a clean git worktree used to run the suite at one commit); the file is read, never copied.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from agent.llm.budget import BudgetedAdapter, BudgetLimits, DailyBudget
from agent.llm.config import build_adapter, load_settings
from data_engineering.pipelines.env import parse_env
from eval.paths import ROOT, SPEND_LEDGER

CAP_USD = 3.00
LEDGER_DAY = datetime(2026, 9, 26, tzinfo=UTC)  # pinned: the counter is a running total, not a daily one
LLM_ENV = {"LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-6-luna", "LLM_REASONING_EFFORT": "none"}


def ledger(cap_usd: float = CAP_USD, path: Path = SPEND_LEDGER) -> DailyBudget:
    path.parent.mkdir(parents=True, exist_ok=True)
    return DailyBudget(BudgetLimits(max_calls=1_000_000, max_usd=cap_usd, state_file=path), now=lambda: LEDGER_DAY)


def paid_adapter(budget: DailyBudget) -> BudgetedAdapter:
    """gpt-6-luna with reasoning_effort none, wrapped by the cap. Settings come from .env plus LLM_ENV."""
    env_file = Path(os.environ.get("CAUTELA_ENV_FILE") or ROOT / ".env")
    env = parse_env(env_file.read_text(encoding="utf-8")) if env_file.is_file() else {}
    settings = load_settings({**env, **LLM_ENV})
    return BudgetedAdapter(build_adapter(settings), budget)


def remaining(budget: DailyBudget) -> float:
    status = budget.status()
    return round(budget.limits.max_usd - status["usd_estimated"], 6)

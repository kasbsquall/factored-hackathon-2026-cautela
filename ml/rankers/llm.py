"""Optional LLM ranker (Anthropic API) with a versioned prompt and structured output.

Runs only when ``ANTHROPIC_API_KEY`` is set (environment or the git-ignored
``.env``); the key is never printed or logged. Before any request:

* the description goes through ``agent.security.pii.mask_text`` (emails, card
  and account numbers, phones, document numbers, known names). If that module
  cannot be imported the ranker refuses to run (fail closed);
* candidates are reduced to the minimum fields (date, amount, currency, type,
  channel, merchant, city) and their ids are replaced by labels C1..Cn, so no
  transaction or customer identifier leaves the service.

The model returns a score per label through a JSON schema; unknown labels are
ignored and missing ones score 0. Token usage is kept for cost reporting.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ml.rankers.protocol import order

PROMPT_PATH = Path(__file__).parent / "prompts" / "rank_v1.md"
DEFAULT_MODEL = os.environ.get("CAUTELA_RANKER_MODEL", "claude-sonnet-5")
# USD per million tokens (input, output). Source: Anthropic published list prices as cached on 2026-06-24.
PRICING = {"claude-sonnet-5": (2.00, 10.00), "claude-opus-5": (5.00, 25.00), "claude-haiku-4-5": (1.00, 5.00)}
SEND_FIELDS = ("amount", "currency", "transaction_type", "channel", "merchant_name", "transaction_city")

SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {"type": "array", "items": {
            "type": "object",
            "properties": {"candidate": {"type": "string"}, "score": {"type": "number"}},
            "required": ["candidate", "score"], "additionalProperties": False}},
    },
    "required": ["scores"],
    "additionalProperties": False,
}


def load_prompt() -> tuple[str, str]:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    first, _, body = text.partition("\n")
    return first.split(":", 1)[1].strip(), body.strip()


def availability() -> tuple[bool, str]:
    """(runnable, reason). Loads .env without echoing values."""
    try:
        from data_engineering.pipelines.env import load_env_file
        load_env_file(".env")
    except ImportError:
        pass
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "ANTHROPIC_API_KEY is not set"
    try:
        import anthropic  # noqa: F401
        from agent.security.pii import mask_text  # noqa: F401
    except ImportError as exc:
        return False, f"dependency unavailable: {exc.name}"
    return True, "ok"


def build_payload(features: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]],
                  known_names: Sequence[str] = ()) -> tuple[str, dict[str, str]]:
    """Masked user message and the label -> transaction_id map (kept local)."""
    from agent.security.pii import mask_text

    labels = {f"C{i + 1}": c["transaction_id"] for i, c in enumerate(candidates)}
    rows = []
    for label, c in zip(labels, candidates):
        row = {"label": label, "date": str(c.get("transaction_date") or c.get("ts"))[:10]}
        row.update({k: c.get(k) for k in SEND_FIELDS})
        rows.append(row)
    message = json.dumps({"reference_date": str(features.get("report_date"))[:10],
                          "description": mask_text(str(features.get("text") or ""), known_names),
                          "candidates": rows}, ensure_ascii=False)
    return message, labels


@dataclass
class LLMRanker:
    model: str = DEFAULT_MODEL
    client: Any = None
    name: str = "llm"
    usage: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.prompt_version, self.system = load_prompt()
        self.name = f"llm_{self.model}_{self.prompt_version}"
        if self.client is None:
            import anthropic
            self.client = anthropic.Anthropic(max_retries=2, timeout=60.0)

    def rank(self, features: Any, candidates: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
        if not candidates:
            return []
        feats = features if isinstance(features, Mapping) else {"text": getattr(features, "text", ""),
                                                                 "report_date": getattr(features, "report_date", "")}
        message, labels = build_payload(feats, candidates)
        response = self.client.messages.create(
            model=self.model, max_tokens=2000, system=self.system,
            messages=[{"role": "user", "content": message}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        usage = getattr(response, "usage", None)
        self.usage.append({"input_tokens": getattr(usage, "input_tokens", 0),
                           "output_tokens": getattr(usage, "output_tokens", 0)})
        if getattr(response, "stop_reason", None) == "refusal":
            raise RuntimeError("model refused the request")
        text = next(b.text for b in response.content if getattr(b, "type", "") == "text")
        scores = {labels[s["candidate"]]: min(1.0, max(0.0, float(s["score"])))
                  for s in json.loads(text)["scores"] if s.get("candidate") in labels}
        ids = [c["transaction_id"] for c in candidates]
        return order(ids, [scores.get(i, 0.0) for i in ids], candidates)

    def cost_usd(self) -> float | None:
        if self.model not in PRICING:
            return None
        pin, pout = PRICING[self.model]
        return sum(u["input_tokens"] * pin + u["output_tokens"] * pout for u in self.usage) / 1e6

"""Per-call cost estimates from agent/llm/prices.yaml.

Prices are never guessed. An entry with a null price, or a provider and model with no entry, yields
cost_usd None and cost_status "price_unknown", which reports then show as "not defined".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PRICES_PATH = Path(__file__).with_name("prices.yaml")


@dataclass(frozen=True)
class PriceEntry:
    provider: str
    model: str
    input_per_mtok: float | None
    output_per_mtok: float | None
    source: str


class PriceTable:
    def __init__(self, entries: list[PriceEntry]) -> None:
        self.entries = entries

    @classmethod
    def load(cls, path: str | Path = PRICES_PATH) -> PriceTable:
        data: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls([PriceEntry(str(e["provider"]), str(e["model"]), e.get("input_per_mtok"),
                               e.get("output_per_mtok"), str(e.get("source") or "TODO"))
                    for e in data.get("prices", [])])

    def find(self, provider: str, model: str) -> PriceEntry | None:
        exact = [e for e in self.entries if e.provider == provider and e.model == model]
        # A dated snapshot returned by the API (gpt-4o-mini-2024-07-18) is priced as its alias row.
        exact = exact or sorted((e for e in self.entries if e.provider == provider and e.model != "*"
                                 and model.startswith(e.model + "-")), key=lambda e: -len(e.model))
        wildcard = [e for e in self.entries if e.provider == provider and e.model == "*"]
        return (exact or wildcard or [None])[0]

    def cost(self, provider: str, model: str, input_tokens: int | None,
             output_tokens: int | None) -> tuple[float | None, str]:
        """Return (estimated USD, status). Status: estimated, price_unknown or tokens_unknown."""
        entry = self.find(provider, model)
        if entry is None or entry.input_per_mtok is None or entry.output_per_mtok is None:
            return None, "price_unknown"
        if input_tokens is None or output_tokens is None:
            return None, "tokens_unknown"
        usd = (input_tokens * entry.input_per_mtok + output_tokens * entry.output_per_mtok) / 1_000_000
        return round(usd, 8), "estimated"

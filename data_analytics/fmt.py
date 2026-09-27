"""Number formats for the report, so every figure is rounded the same way and carries its unit.

counts: thousands separators; rates: one decimal percent with count / denominator; hours and days: one decimal;
USD per dispute and per unit of work: two decimals; USD per year: whole dollars; LLM USD: six decimals.
"""

from __future__ import annotations


def n(x) -> str:
    return "n/a" if x is None else f"{x:,}" if isinstance(x, int) else f"{x:,.1f}"


def pct(k, d, digits: int = 1) -> str:
    return "n/a" if not d or k is None else f"{100 * k / d:.{digits}f}% ({k:,} / {d:,})"


def rate(v, digits: int = 1) -> str:
    return "n/a" if v is None else f"{100 * v:.{digits}f}%"


def usd(v) -> str:
    return "n/a" if v is None else f"USD {v:,.2f}"


def usd_year(v) -> str:
    return "n/a" if v is None else f"USD {v:,.0f}"


def usd_llm(v) -> str:
    return "n/a" if v is None else f"USD {v:.6f}"


def table(headers: list[str], body: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in body]
    return "\n".join(lines)

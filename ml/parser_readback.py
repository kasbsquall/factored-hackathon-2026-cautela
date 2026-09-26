"""Parser read-back: how often the parser recovers the amount and date a description was generated from.

    uv run python -m ml.parser_readback --out ml/reports/parser_readback.json

Measured on val (seen families) and on test by family. This is a diagnostic, not a tuning loop: the
parser vocabulary is documented in ml/features/lexicon.py and ml/README.md, and test numbers are only
reported after a change, never used to choose one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date
from pathlib import Path

from ml.data import load_cases
from ml.features.parse import parse_description


def readback(cases: list[dict], key) -> dict:
    out: dict = {}
    for c in cases:
        p = parse_description(c["description"], date.fromisoformat(c["report_date"]))
        h = c["hints"]
        slot = out.setdefault(key(c), {"amount_given": 0, "amount_read": 0, "date_given": 0, "date_read": 0})
        if "amount" in h:
            slot["amount_given"] += 1
            slot["amount_read"] += bool(p.amount and abs(p.amount / h["amount"]["claimed"] - 1) < 0.01)
        if "date" in h:
            slot["date_given"] += 1
            slot["date_read"] += bool(p.date_lo and p.date_lo.isoformat() == h["date"]["lo"]
                                      and p.date_hi.isoformat() == h["date"]["hi"])
    for slot in out.values():
        slot["amount_rate"] = round(slot["amount_read"] / slot["amount_given"], 4) if slot["amount_given"] else None
        slot["date_rate"] = round(slot["date_read"] / slot["date_given"], 4) if slot["date_given"] else None
    return dict(sorted(out.items()))


def measure() -> dict:
    val, test = load_cases("val"), load_cases("test")
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    return {
        "parser_commit": commit,
        "val_by_language": readback(val, lambda c: c["language"]),
        "test_by_language_and_family_split": readback(
            test, lambda c: f"{c['language']}/{'held_out' if c['family_heldout'] else 'seen'}"),
        "test_by_family": readback(test, lambda c: c["family"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("ml/reports/parser_readback.json"))
    args = ap.parse_args()
    result = measure()
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

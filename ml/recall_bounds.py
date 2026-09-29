"""How far one misremembered detail lies from the charge it describes, fitted on train.

The labels (ml/scenarios/hints.py) call a charge near-consistent when a description with three or more cues misses it
on exactly one; the ``match`` cases with a ``recall_error`` are those. On train, the cue the description gets wrong
about its target is measured with the features the rankers read (ml/features/pairwise.py): for a date, the days
beyond the stated range (``date_dist``); for an amount, the ratio between charge and stated amount
(``exp(amt_logdiff)``). The largest of each on train is the bound.

The service uses it where the labels have no rule: a description with only two cues (amount and date, say) that fits
a charge on one and misses the other by no more than one misremembered detail. Such a charge is shown in a numbered
list instead of transferring the customer (agent/orchestrator/disposition.plausible_charges); only the customer's
explicit pick followed by "I don't recognize it" leads to a write. ``fit`` refuses any split but train. The report
also measures, on val, the share of recall-error targets within the bound and how many no_match descriptions with two
cues would show a charge (one extra question, no write).

    uv run python -m ml.recall_bounds         # fit on train, check on val, write ml/reports/recall_bounds.json
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence

from ml.data import REPORTS_DIR, load_cases
from ml.decision import _require_split
from ml.disposition import IDX, _cue_hits
from ml.features.pairwise import candidate_features
from ml.rankers.protocol import coerce_features

# Fitted by `python -m ml.recall_bounds` on train (ml/reports/recall_bounds.json): the largest date distance and
# amount ratio of a misremembered cue there, 12 days and 1.799 (rounded up to 1.80).
DATE_DAYS = 12
AMOUNT_RATIO = 1.80
REPORT = REPORTS_DIR / "recall_bounds.json"


def one_detail_off(row: Sequence[float], hits: Mapping[str, bool], date_days: float = DATE_DAYS,
                   amount_ratio: float = AMOUNT_RATIO) -> bool:
    """Two cues read, one fits, and the other is an amount or a date within one misremembered detail of the charge.
    An amount in another currency than the one stated is not an amount off by a ratio."""
    if len(hits) != 2 or list(hits.values()).count(False) != 1:
        return False
    missed = next(k for k, v in hits.items() if not v)
    if missed == "date":
        return row[IDX["date_dist"]] <= date_days
    if missed == "amount":
        currency_ok = not (row[IDX["cur_given"]] and not row[IDX["cur_match"]])
        return currency_ok and math.exp(row[IDX["amt_logdiff"]]) <= amount_ratio
    return False


def _rows(case: dict) -> tuple[list[dict], list[list[float]]]:
    parsed, report = coerce_features({"text": case["description"], "report_date": case["report_date"]})
    cands = case["candidates"]
    return cands, candidate_features(parsed, cands, report) if cands else []


def target_miss(case: dict) -> tuple[str, float] | None:
    """The one cue the description misses its target on, and by how much (days or amount ratio)."""
    cands, rows = _rows(case)
    ids = [c["transaction_id"] for c in cands]
    if case.get("target_transaction_id") not in ids:
        return None
    row = rows[ids.index(case["target_transaction_id"])]
    missed = [k for k, v in _cue_hits(row).items() if not v]
    if len(missed) != 1 or missed[0] not in ("date", "amount"):
        return None
    if missed[0] == "date":
        return "date", row[IDX["date_dist"]]
    return "amount", math.exp(row[IDX["amt_logdiff"]])


def fit(cases: list[dict]) -> dict:
    _require_split(cases, "train", "the recall bounds")
    misses = [m for c in cases if c.get("recall_error") for m in [target_miss(c)] if m]
    dates = [v for k, v in misses if k == "date"]
    amounts = [v for k, v in misses if k == "amount"]
    return {"date_days": max(dates), "amount_ratio": round(max(amounts), 4),
            "n_date": len(dates), "n_amount": len(amounts)}


def check(cases: list[dict], date_days: float, amount_ratio: float) -> dict:
    """Coverage of recall-error targets, and no_match descriptions with two cues where some charge is one detail
    off (they would get a numbered list, then "none of these", then the transfer)."""
    misses = [m for c in cases if c.get("recall_error") for m in [target_miss(c)] if m]
    within = sum(v <= (date_days if k == "date" else amount_ratio) for k, v in misses)
    two_cue_no_match = shown = 0
    for c in cases:
        if c["label"] != "no_match":
            continue
        cands, rows = _rows(c)
        hits = [_cue_hits(r) for r in rows]
        if not hits or len(hits[0]) != 2:
            continue
        two_cue_no_match += 1
        shown += any(one_detail_off(r, h, date_days, amount_ratio) for r, h in zip(rows, hits))
    return {"recall_error_targets": len(misses), "within_bound": within,
            "no_match_two_cues": two_cue_no_match, "no_match_two_cues_shown_a_charge": shown}


def main() -> None:
    fitted = fit(load_cases("train"))
    report = {"fitted_on": "train", **fitted, "service_constants": {"date_days": DATE_DAYS,
                                                                    "amount_ratio": AMOUNT_RATIO},
              "val_check": check(load_cases("val"), DATE_DAYS, AMOUNT_RATIO)}
    REPORT.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

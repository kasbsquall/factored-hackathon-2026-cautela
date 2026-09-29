"""The one-misremembered-detail bounds: fitted on train only, and the service constants are the fitted values."""

from __future__ import annotations

import json
import math

import pytest

from ml import recall_bounds
from ml.data import CASES_DIR, load_cases
from ml.disposition import IDX
from ml.features.pairwise import FEATURE_NAMES


def _case(split: str, text: str, amount: float, day: str) -> dict:
    tx = {"transaction_id": "T1", "amount": amount, "currency": "USD", "transaction_date": f"2026-03-{day} 10:00:00",
          "transaction_type": "Purchase", "transaction_status": "Approved", "channel": "POS", "merchant_name": None,
          "transaction_city": "Lima"}
    return {"split": split, "label": "match", "recall_error": "date", "description": text, "report_date": "2026-04-01",
            "candidates": [tx], "target_transaction_id": "T1"}


def test_fit_refuses_any_split_but_train():
    with pytest.raises(ValueError):
        recall_bounds.fit([_case("val", "una compra de 100 dolares el 10 de marzo", 100.0, "20")])


def test_fit_takes_the_largest_miss_of_each_kind():
    cases = [_case("train", "una compra de 100 dolares el 10 de marzo en tienda", 100.0, "20"),  # date 10 days off
             _case("train", "una compra de 100 dolares el 10 de marzo en tienda", 100.0, "15"),  # 5 days off
             _case("train", "una compra de 100 dolares el 10 de marzo en tienda", 150.0, "10")]  # amount 1.5x
    fitted = recall_bounds.fit(cases)
    assert (fitted["date_days"], fitted["amount_ratio"], fitted["n_date"], fitted["n_amount"]) == (10.0, 1.5, 2, 1)


def _row(**values: float) -> list[float]:
    row = [0.0] * len(FEATURE_NAMES)
    for name, value in values.items():
        row[IDX[name]] = value
    return row


def test_one_detail_off_needs_exactly_two_cues_and_a_bounded_amount_or_date_miss():
    off = recall_bounds.one_detail_off
    assert off(_row(date_dist=12), {"amount": True, "date": False})
    assert not off(_row(date_dist=13), {"amount": True, "date": False})
    assert off(_row(amt_logdiff=math.log(1.8)), {"amount": False, "type": True})
    assert not off(_row(amt_logdiff=math.log(1.9)), {"amount": False, "type": True})
    assert not off(_row(amt_logdiff=0.0, cur_given=1, cur_match=0), {"amount": False, "date": True})  # currency
    assert not off(_row(), {"amount": True, "type": False})  # a type has no "near"
    assert not off(_row(date_dist=1), {"amount": True, "date": False, "type": True})  # three cues: label rule
    assert not off(_row(date_dist=1), {"amount": False, "date": False})


def test_the_service_constants_are_the_fitted_ones():
    report = json.loads(recall_bounds.REPORT.read_text(encoding="utf-8"))
    assert report["fitted_on"] == "train"
    assert recall_bounds.DATE_DAYS == report["date_days"]
    assert recall_bounds.AMOUNT_RATIO == math.ceil(report["amount_ratio"] * 100) / 100
    assert report["service_constants"] == {"date_days": recall_bounds.DATE_DAYS,
                                           "amount_ratio": recall_bounds.AMOUNT_RATIO}


@pytest.mark.skipif(not (CASES_DIR / "train.jsonl").exists(), reason="case files are not committed")
def test_the_committed_report_reproduces_from_train():
    report = json.loads(recall_bounds.REPORT.read_text(encoding="utf-8"))
    fitted = recall_bounds.fit(load_cases("train"))
    assert {k: report[k] for k in fitted} == fitted

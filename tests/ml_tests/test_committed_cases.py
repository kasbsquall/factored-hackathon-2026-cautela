"""Leakage and label checks on the rebuilt case files in eval/cases/disputes."""

from __future__ import annotations

import hashlib
import json

import pytest

from ml.data import CASES_DIR, load_cases
from ml.scenarios.build import BuildConfig
from ml.scenarios.render import HELDOUT_FAMILIES
from tests.ml_tests.label_audit import audit

SPLITS = ("train", "val", "test")
pytestmark = pytest.mark.skipif(
    not all((CASES_DIR / f"{s}.jsonl").exists() for s in ("train", "val", "test")),
    reason="case files are not committed; rebuild with: uv run python -m ml.scenarios.build --verify")


@pytest.fixture(scope="module")
def cases():
    return {s: load_cases(s) for s in SPLITS}


def test_manifest_hashes_match_files():
    manifest = json.loads((CASES_DIR / "manifest.json").read_text(encoding="utf-8"))
    for s in SPLITS:
        payload = (CASES_DIR / f"{s}.jsonl").read_bytes().replace(b"\r\n", b"\n")  # tolerate CRLF checkouts
        assert hashlib.sha256(payload).hexdigest() == manifest["file_sha256"][s]


def test_group_split_has_no_customer_or_transaction_overlap(cases):
    cust = {s: {c["customer_ref"] for c in cases[s]} for s in SPLITS}
    tx = {s: {t["transaction_id"] for c in cases[s] for t in c["candidates"]} for s in SPLITS}
    for a, b in (("train", "val"), ("train", "test"), ("val", "test")):
        assert not cust[a] & cust[b], f"customers shared by {a} and {b}"
        assert not tx[a] & tx[b], f"transactions shared by {a} and {b}"


def test_time_split(cases):
    periods = BuildConfig().periods
    for s in SPLITS:
        assert all(periods[s][0] <= c["report_date"] <= periods[s][1] for c in cases[s])
    assert max(c["report_date"] for c in cases["train"]) < min(c["report_date"] for c in cases["val"])
    assert max(c["report_date"] for c in cases["val"]) < min(c["report_date"] for c in cases["test"])


def test_portuguese_cases_share_split_and_content_with_source(cases):
    for s in SPLITS:
        by_id = {c["case_id"]: c for c in cases[s]}
        pt = [c for c in cases[s] if c["language"] == "pt"]
        assert pt, f"no Portuguese cases in {s}"
        for c in pt:
            src = by_id.get(c["source_case_id"])
            assert src is not None, f"{c['case_id']} source not in the same split"
            assert (src["customer_ref"], src["label"], src["candidates"]) == (c["customer_ref"], c["label"], c["candidates"])


def test_heldout_families_absent_from_train_and_val(cases):
    for s in ("train", "val"):
        assert not {c["family"] for c in cases[s]} & set(HELDOUT_FAMILIES)
    assert {c["family"] for c in cases["test"]} >= set(HELDOUT_FAMILIES)


def test_labels_follow_the_documented_rule(cases):
    problems = [p for s in SPLITS for c in cases[s] for p in audit(c)]
    assert problems == [], problems[:5]


def test_no_raw_customer_identifiers(cases):
    raw = [c for s in SPLITS for c in cases[s] if "customer_id" in c or not c["customer_ref"].startswith("cust_")]
    assert raw == []


FRESH = "test_fresh"
needs_fresh = pytest.mark.skipif(not (CASES_DIR / f"{FRESH}.jsonl").exists(),
                                 reason="test_fresh.jsonl is rebuilt by: uv run python -m ml.scenarios.build --verify")


@pytest.fixture(scope="module")
def fresh_cases():
    return load_cases(FRESH)


@needs_fresh
def test_fresh_file_matches_its_frozen_hash():
    manifest = json.loads((CASES_DIR / "manifest.json").read_text(encoding="utf-8"))
    payload = (CASES_DIR / f"{FRESH}.jsonl").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(payload).hexdigest() == manifest[FRESH]["file_sha256"]


@needs_fresh
def test_fresh_shares_no_customer_or_transaction_with_any_split(cases, fresh_cases):
    cust = {c["customer_ref"] for c in fresh_cases}
    tx = {t["transaction_id"] for c in fresh_cases for t in c["candidates"]}
    for s in SPLITS:
        assert not cust & {c["customer_ref"] for c in cases[s]}, f"customers shared with {s}"
        assert not tx & {t["transaction_id"] for c in cases[s] for t in c["candidates"]}, f"transactions shared with {s}"


@needs_fresh
def test_fresh_labels_window_and_families(cases, fresh_cases):
    from ml.scenarios.render import FRESH_FAMILIES

    assert [p for c in fresh_cases for p in audit(c)] == []
    assert min(c["report_date"] for c in fresh_cases) > max(c["report_date"] for c in cases["val"])
    assert {c["family"] for c in fresh_cases} >= set(FRESH_FAMILIES) | set(HELDOUT_FAMILIES)
    assert not {c["family"] for s in SPLITS for c in cases[s]} & set(FRESH_FAMILIES)
    by_id = {c["case_id"]: c for c in fresh_cases}
    assert all(by_id[c["source_case_id"]]["candidates"] == c["candidates"] for c in fresh_cases if c["language"] == "pt")

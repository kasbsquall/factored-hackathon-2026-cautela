"""Scenario builder: determinism, splits, pairing, held-out families and label validity (synthetic input)."""

from __future__ import annotations

import json

import pytest

from ml.data import ranker_input
from ml.scenarios.build import BuildConfig, build, write_outputs
from ml.scenarios.render import HELDOUT_FAMILIES
from tests.ml_tests.label_audit import audit
from tests.ml_tests.synthetic import make_world

SMALL = dict(n_es={"train": 60, "val": 12, "test": 18}, min_per_stratum={"train": 5, "val": 1, "test": 1})


@pytest.fixture(scope="module")
def world():
    return make_world()


@pytest.fixture(scope="module")
def built(world):
    customers, txs = world
    return build(customers, txs, BuildConfig(**SMALL))


def _dump(cases: dict) -> str:
    return json.dumps(cases, sort_keys=True, default=str)


def test_build_is_deterministic(world, built, tmp_path):
    customers, txs = world
    again, _ = build(customers, txs, BuildConfig(**SMALL))
    assert _dump(again) == _dump(built[0])
    m1 = write_outputs(built[0], built[1], BuildConfig(**SMALL), tmp_path / "a", {})
    m2 = write_outputs(again, built[1], BuildConfig(**SMALL), tmp_path / "b", {})
    assert m1["data_version"] == m2["data_version"]
    assert (tmp_path / "a" / "test.jsonl").read_bytes() == (tmp_path / "b" / "test.jsonl").read_bytes()


def test_seed_changes_output(world, built):
    customers, txs = world
    other, _ = build(customers, txs, BuildConfig(seed=7, **SMALL))
    assert _dump(other) != _dump(built[0])


def test_builder_output_does_not_depend_on_input_order(world, built):
    customers, txs = world
    shuffled, _ = build(list(reversed(customers)), dict(reversed(list(txs.items()))), BuildConfig(**SMALL))
    assert _dump(shuffled) == _dump(built[0])


def test_no_customer_or_transaction_overlap_across_splits(built):
    cases = built[0]
    customers = {s: {c["customer_ref"] for c in cases[s]} for s in cases}
    tx = {s: {t["transaction_id"] for c in cases[s] for t in c["candidates"]} for s in cases}
    for a, b in (("train", "val"), ("train", "test"), ("val", "test")):
        assert not customers[a] & customers[b]
        assert not tx[a] & tx[b]


def test_report_dates_respect_time_split(built):
    cfg = BuildConfig(**SMALL)
    for split, cases in built[0].items():
        lo, hi = cfg.periods[split]
        assert all(lo <= c["report_date"] <= hi for c in cases)


def test_portuguese_twin_stays_with_its_source(built):
    for split, cases in built[0].items():
        by_id = {c["case_id"]: c for c in cases}
        for c in (c for c in cases if c["language"] == "pt"):
            src = by_id[c["source_case_id"]]
            assert src["language"] == "es" and src["split"] == c["split"] == split
            for key in ("customer_ref", "label", "target_transaction_id", "candidates", "hints", "family"):
                assert c[key] == src[key]
            assert "TEAM-GENERATED" in c["text_origin"]


def test_heldout_families_only_in_test(built):
    for split in ("train", "val"):
        assert not {c["family"] for c in built[0][split]} & set(HELDOUT_FAMILIES)
    assert all(c["family_heldout"] == (c["family"] in HELDOUT_FAMILIES) for c in built[0]["test"])


def test_every_label_follows_the_documented_rule(built):
    problems = [p for cases in built[0].values() for c in cases for p in audit(c)]
    assert problems == []


def test_every_label_kind_is_produced(built):
    labels = {c["label"] for cases in built[0].values() for c in cases}
    assert labels == {"match", "ambiguous", "no_match"}


def test_target_belongs_to_the_candidate_pool(built):
    for cases in built[0].values():
        for c in cases:
            ids = {t["transaction_id"] for t in c["candidates"]}
            assert c["target_transaction_id"] is None or c["target_transaction_id"] in ids
            assert len(ids) == len(c["candidates"]) >= BuildConfig().min_pool


def test_ranker_input_hides_hints_and_labels(built):
    case = built[0]["train"][0]
    assert set(ranker_input(case)) == {"text", "report_date", "language"}

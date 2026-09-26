"""The fresh test split builder (ml/scenarios/fresh.py) and family F7, on synthetic input."""

from __future__ import annotations

import json
import random

import pytest

from ml.scenarios import fresh
from ml.scenarios.build import BuildConfig, build, customers_to_load, split_of
from ml.scenarios.render import FRESH_FAMILIES, render
from tests.ml_tests.label_audit import audit
from tests.ml_tests.synthetic import make_world

SMALL = fresh.FreshConfig(n_es=40, min_per_stratum=1, period=("2025-01-01", "2026-06-17"))


@pytest.fixture(scope="module")
def world():
    return make_world()


@pytest.fixture(scope="module")
def test_bucket(world):
    customers, _ = world
    return [c for c in customers if split_of(c["customer_id"], SMALL.bucket_seed) == "test"]


@pytest.fixture(scope="module")
def built(world, test_bucket):
    return fresh.build_fresh(test_bucket, world[1], SMALL)


def test_fresh_build_is_deterministic_and_seeded(world, test_bucket, built):
    again, _ = fresh.build_fresh(list(reversed(test_bucket)), world[1], SMALL)
    assert fresh.payload(again) == fresh.payload(built[0])
    other, _ = fresh.build_fresh(test_bucket, world[1], fresh.FreshConfig(**{**SMALL.__dict__, "seed": 7}))
    assert fresh.payload(other)[1] != fresh.payload(built[0])[1]


def test_fresh_uses_another_seed_than_the_original_build():
    assert fresh.FreshConfig().seed != BuildConfig().seed
    assert fresh.FreshConfig().bucket_seed == BuildConfig().seed


def test_eligible_customers_are_test_bucket_and_never_loaded_by_the_original_build():
    customers = [{"customer_id": f"CLI-X{i:05d}", "country": ["MX", "CO", "AR"][i % 3],
                  "segment": ["Basic", "Plus", "Premium", "Student"][i % 4]} for i in range(6000)]
    original = BuildConfig(n_es={"train": 6, "val": 2, "test": 2}, min_per_stratum={"train": 0, "val": 0, "test": 0})
    eligible = fresh.eligible_customers(customers, fresh.FreshConfig(), original)
    loaded = set(customers_to_load(customers, original))
    assert eligible and all(split_of(c["customer_id"], BuildConfig().seed) == "test" for c in eligible)
    assert not {c["customer_id"] for c in eligible} & loaded
    assert eligible == fresh.eligible_customers(list(reversed(customers)), fresh.FreshConfig(), original)


def test_fresh_cases_follow_the_label_rule_and_mark_f7_held_out(built):
    cases = built[0]
    assert cases and all(c["split"] == fresh.SPLIT for c in cases)
    assert [p for c in cases for p in audit(c)] == []
    assert {c["family"] for c in cases} >= set(FRESH_FAMILIES)
    assert all(c["family_heldout"] == (c["family"] not in ("F1", "F2", "F3", "F4")) for c in cases)
    lo, hi = SMALL.period
    assert all(lo <= c["report_date"] <= hi for c in cases)


def test_portuguese_twins_share_their_source(built):
    by_id = {c["case_id"]: c for c in built[0]}
    pt = [c for c in built[0] if c["language"] == "pt"]
    assert pt
    for c in pt:
        src = by_id[c["source_case_id"]]
        assert (src["customer_ref"], src["label"], src["candidates"], src["family"]) == \
               (c["customer_ref"], c["label"], c["candidates"], c["family"])


def test_original_splits_never_render_f7(world):
    customers, txs = world
    cases, _ = build(customers, txs, BuildConfig(n_es={"train": 60, "val": 12, "test": 18},
                                                 min_per_stratum={"train": 5, "val": 1, "test": 1}))
    assert not {c["family"] for split in cases.values() for c in split} & set(FRESH_FAMILIES)


@pytest.mark.parametrize("lang,region", [("es", "MX"), ("es", "CO"), ("es", "AR"), ("pt", "MX")])
def test_f7_renders_every_cue_kind(lang, region):
    hints = {"amount": {"claimed": 35000.0, "exact": False, "currency": "COP"},
             "date": {"kind": "days_ago", "n": 4, "lo": "2026-05-01", "hi": "2026-05-03"},
             "merchant": {"form": "exact", "surface": "Farmacia Salud", "compatible": ["Farmacia Salud"]},
             "type": {"value": "Purchase"}, "channel": {"value": "POS"}, "city": {"value": "Bogotá"}}
    text = render(hints, lang, region, "F7", random.Random(1))
    assert "Farmacia Salud" in text and "Bogotá" in text
    assert ("cuatro" if lang == "es" else "quatro") in text


def test_fresh_manifest_section_and_verification(built):
    section = fresh.manifest_section(built[0], built[1], SMALL, 10, 5, {"rows_kept": 1})
    assert section["file_sha256"] == fresh.payload(built[0])[1]
    assert fresh.verify_problems({fresh.SPLIT: section}, built[0]) == []
    assert fresh.verify_problems({fresh.SPLIT: section}, built[0][1:]) != []
    assert fresh.verify_problems({}, built[0]) != []
    json.dumps(section)  # serializable as written to manifest.json


def test_evaluation_of_test_fresh_is_one_shot_and_hash_checked(tmp_path, monkeypatch, built):
    from ml import evaluate

    text, sha = fresh.payload(built[0])
    (tmp_path / "test_fresh.jsonl").write_text(text, encoding="utf-8", newline="\n")
    (tmp_path / "manifest.json").write_text(json.dumps({"test_fresh": {"file_sha256": sha}}), encoding="utf-8")
    monkeypatch.setattr(evaluate, "CASES_DIR", tmp_path)
    out = tmp_path / "results_fresh.json"
    evaluate.check_fresh_frozen(out)  # frozen file, no results yet: allowed
    out.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="evaluated once"):
        evaluate.check_fresh_frozen(out)
    out.unlink()
    (tmp_path / "test_fresh.jsonl").write_text(text + text, encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit, match="does not match"):
        evaluate.check_fresh_frozen(out)

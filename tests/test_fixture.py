import hashlib
import json

import duckdb
import pyarrow.parquet as pq
import pytest

from data_engineering.fixtures.generate import LABEL, generate

from .conftest import SEED


def _digest(root) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_manifest_is_labeled_as_synthetic(fixture_source):
    _, manifest = fixture_source
    assert manifest["label"] == LABEL == "SYNTHETIC TEST FIXTURE, team-generated, not organizer data"
    assert manifest["seed"] == SEED
    assert len(manifest["tables"]) == 13


def test_generator_is_deterministic_for_a_seed(tmp_path, fixture_source):
    source, manifest = fixture_source
    again = generate(tmp_path / "again", SEED)
    assert again == manifest
    assert _digest(tmp_path / "again") == _digest(source)


def test_different_seed_gives_different_data(tmp_path, fixture_source):
    source, _ = fixture_source
    other = tmp_path / "other"
    generate(other, SEED + 1)
    rel = "customers/snapshot_date=2026-05-31/part-0.parquet"
    assert _digest(other)[rel] != _digest(source)[rel]


def test_refuses_to_overwrite_foreign_directory(tmp_path):
    target = tmp_path / "not_a_fixture"
    target.mkdir()
    (target / "keep.txt").write_text("user data")
    with pytest.raises(SystemExit):
        generate(target, SEED)
    assert (target / "keep.txt").exists()


def test_duplicate_rate_is_about_two_percent(fixture_source):
    _, manifest = fixture_source
    tx = manifest["tables"]["transactions"]
    base = tx["rows_written"] - tx["exact_duplicates"] - tx["late_updates"] - tx["quarantine"]["null_pk"]
    assert tx["exact_duplicates"] / base == pytest.approx(0.02, abs=0.001)


def test_schema_evolution_column_only_in_late_partitions(fixture_source):
    source, manifest = fixture_source
    early = pq.read_schema(source / "transactions/year=2026/month=04/day=01/part-0.parquet")
    late = pq.read_schema(source / "transactions/year=2026/month=05/day=30/part-0.parquet")
    assert "installments" not in early.names and "installments" in late.names
    assert manifest["tables"]["transactions"]["drift"]["unexpected_column"]["installments"] == 15


def test_dispute_complaints_point_to_matching_transactions(fixture_source):
    source, manifest = fixture_source
    def rows(table: str) -> list[dict]:
        rel = duckdb.sql(f"SELECT * FROM read_parquet('{source.as_posix()}/{table}/**/*.parquet', "
                         "union_by_name = true, hive_partitioning = false)")
        return [dict(zip(rel.columns, r)) for r in rel.fetchall()]

    by_tx = {t["transaction_id"]: t for t in rows("transactions") if t["transaction_id"]}
    by_cl = {c["complaint_id"]: c for c in rows("complaints") if c["complaint_id"]}
    assert len(manifest["dispute_links"]) > 100
    for complaint_id, tx_id in manifest["dispute_links"].items():
        complaint, charge = by_cl[complaint_id], by_tx[tx_id]
        if complaint["customer_id"].startswith("ORPHAN"):
            continue  # injected orphan key
        assert complaint["customer_id"] == charge["customer_id"]
        assert complaint["creation_date"] > charge["transaction_date"]
        if complaint["claimed_amount"] is not None and "," not in charge["amount"]:
            assert complaint["claimed_amount"] == pytest.approx(float(charge["amount"]))


def test_manifest_is_valid_json_on_disk(fixture_source):
    source, manifest = fixture_source
    assert json.loads((source / "manifest.json").read_text(encoding="utf-8")) == manifest

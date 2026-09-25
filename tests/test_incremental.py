"""Update correctness with static files: incremental loads must converge to the same silver as one full load.

The fixture is delivered in two waves. Wave 1 holds every partition up to SPLIT except one withheld early partition.
Wave 2 adds the rest: later partitions (carrying late re-delivered versions of wave-1 rows) and the withheld file,
which lands in a partition older than the table's high-water mark.
"""

import shutil
from datetime import date
from pathlib import Path

import pytest

from data_engineering.pipelines.run import main, run_pipeline

from .conftest import scalar, silver_rows

SPLIT = date(2026, 5, 10)
WITHHELD = "transactions/year=2026/month=04/day=20/"


def _day(rel: str) -> date | None:
    parts = dict(p.split("=") for p in rel.split("/") if "=" in p)
    if "year" not in parts:
        return None
    return date(int(parts["year"]), int(parts["month"]), int(parts["day"]))


def _stage(src: Path, dst: Path, wave: int) -> None:
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src).as_posix()
        if path.is_dir():
            continue
        day = _day(rel)
        in_first = day is None or (day <= SPLIT and not rel.startswith(WITHHELD))
        if wave == 2 or in_first:
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


@pytest.fixture(scope="module")
def two_waves(tmp_path_factory, fixture_source):
    source, _ = fixture_source
    work = tmp_path_factory.mktemp("incremental")
    staged, db = work / "landing", work / "warehouse.duckdb"
    _stage(source, staged, wave=1)
    first = run_pipeline(str(staged), db, reports_dir=work / "reports")
    _stage(source, staged, wave=2)
    second = run_pipeline(str(staged), db, reports_dir=work / "reports")
    return first, second, db


@pytest.fixture()
def copied_full_db(tmp_path, full_run):
    _, db = full_run
    target = tmp_path / "copy.duckdb"
    shutil.copy2(db, target)
    return target


def test_incremental_converges_to_full_load(two_waves, full_run, contracts):
    _, _, inc_db = two_waves
    _, full_db = full_run
    for name, contract in contracts.items():
        assert silver_rows(inc_db, contract) == silver_rows(full_db, contract), name


def test_second_wave_updates_rows_from_first_wave(two_waves, fixture_source):
    first, second, _ = two_waves
    tx2 = second["tables"]["transactions"]
    assert first["tables"]["transactions"]["files"]["new"] + tx2["files"]["new"] == 60
    assert tx2["rows"]["updated"] > 0  # re-delivered versions of rows loaded in wave 1
    assert second["tables"]["complaints"]["rows"]["updated"] > 0
    assert tx2["late_arrivals"]["late_partition_rows"] > 0  # the withheld file, older than the watermark
    assert tx2["high_water_mark"]["before"] == SPLIT.isoformat()


def test_counts_across_waves_equal_manifest(two_waves, fixture_source):
    first, second, db = two_waves
    manifest = fixture_source[1]["tables"]
    for name, expected in manifest.items():
        dupes = sum(r["tables"][name]["rows"]["exact_duplicates"] for r in (first, second))
        assert dupes == expected["exact_duplicates"], name
        quarantined = scalar(db, "SELECT count(*) FROM quarantine.records WHERE table_name = ?", [name])
        assert quarantined == sum(expected["quarantine"].values()), name


def test_rerun_is_idempotent(copied_full_db, fixture_source, full_run, contracts, tmp_path):
    source, _ = fixture_source
    before = {n: silver_rows(copied_full_db, c) for n, c in contracts.items()}
    counts = [scalar(copied_full_db, f"SELECT count(*) FROM {t}")
              for t in ("quarantine.records", "control.file_ledger", "bronze.transactions")]
    report = run_pipeline(str(source), copied_full_db, reports_dir=tmp_path / "reports")
    assert report["totals"]["files_new"] == 0
    assert report["totals"]["bronze_in"] == 0
    assert {n: silver_rows(copied_full_db, c) for n, c in contracts.items()} == before
    assert [scalar(copied_full_db, f"SELECT count(*) FROM {t}")
            for t in ("quarantine.records", "control.file_ledger", "bronze.transactions")] == counts


def test_full_refresh_rebuilds_identically(copied_full_db, fixture_source, full_run, contracts, tmp_path):
    source, manifest = fixture_source
    report = run_pipeline(str(source), copied_full_db, tables=["transactions"], full_refresh=True,
                          reports_dir=tmp_path / "reports")
    assert report["tables_processed"] == ["transactions"]
    assert silver_rows(copied_full_db, contracts["transactions"]) == silver_rows(full_run[1], contracts["transactions"])
    assert scalar(copied_full_db, "SELECT count(*) FROM bronze.transactions") == \
        manifest["tables"]["transactions"]["rows_written"]


def test_cli_runs_a_table_subset(tmp_path, fixture_source, capsys):
    source, _ = fixture_source
    code = main(["--source", str(source), "--target", str(tmp_path / "w.duckdb"), "--tables", "branches,customers",
                 "--reports-dir", str(tmp_path / "reports"), "--env-file", str(tmp_path / "absent.env")])
    assert code == 0
    out = capsys.readouterr().out
    assert "SYNTHETIC TEST FIXTURE" in out and "customers" in out
    assert len(list((tmp_path / "reports").glob("quality_*.json"))) == 1


def test_cli_rejects_bad_source(tmp_path, capsys):
    assert main(["--source", "ftp://example.com/data", "--target", str(tmp_path / "w.duckdb"),
                 "--env-file", str(tmp_path / "absent.env")]) == 1
    assert "unsupported scheme" in capsys.readouterr().err


def test_failed_table_rolls_back_and_rerun_recovers(tmp_path, fixture_source, monkeypatch, contracts):
    """A crash mid-table must leave no half-loaded state: the ledger stays empty so the rerun reloads the files."""
    from data_engineering.pipelines import silver

    source, manifest = fixture_source
    db = tmp_path / "w.duckdb"
    run_pipeline(str(source), db, tables=["branches"], reports_dir=tmp_path / "r")

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated crash during merge")

    monkeypatch.setattr(silver, "merge", boom)
    with pytest.raises(RuntimeError):
        run_pipeline(str(source), db, tables=["branches", "customers"], reports_dir=tmp_path / "r")
    assert scalar(db, "SELECT count(*) FROM control.file_ledger WHERE table_name = 'customers'") == 0
    assert scalar(db, "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'bronze' "
                      "AND table_name = 'customers'") == 0
    assert scalar(db, "SELECT count(*) FROM control.runs WHERE status = 'failed'") == 1

    monkeypatch.undo()
    report = run_pipeline(str(source), db, tables=["branches", "customers"], reports_dir=tmp_path / "r")
    assert report["tables"]["customers"]["rows"]["silver_total"] == manifest["tables"]["customers"]["expected_silver_rows"]
    assert report["tables"]["branches"]["files"]["new"] == 0


def test_gold_is_an_explicit_placeholder():
    from data_engineering.pipelines import gold

    assert gold.GOLD_TABLES == []
    with pytest.raises(NotImplementedError):
        gold.build_gold()

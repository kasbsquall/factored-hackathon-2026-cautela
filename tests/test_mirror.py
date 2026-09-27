"""`make mirror` (pipelines/mirror.py) and the committed load summary (pipelines/load_summary.py), without network."""

import os

import pytest

from data_engineering.pipelines import load_summary
from data_engineering.pipelines.mirror import main as mirror_main
from data_engineering.pipelines.mirror import mirror


def _source(root):
    root.mkdir(parents=True)
    (root / "branches.csv").write_text("branch_id\nB1\n", encoding="utf-8")
    day = root / "complaints" / "year=2026" / "month=06" / "day=17"
    day.mkdir(parents=True)
    (day / "part.csv").write_text("complaint_id\nC1\n", encoding="utf-8")
    os.utime(root / "branches.csv", (1_700_000_000, 1_700_000_000))
    return root


def test_mirror_copies_files_with_their_modification_time(tmp_path):
    src, dest = _source(tmp_path / "src"), tmp_path / "raw"
    result = mirror(str(src), dest, ["branches", "complaints"])
    assert (result["files"], result["copied"]) == (2, 2)
    assert (dest / "complaints" / "year=2026" / "month=06" / "day=17" / "part.csv").read_text() == "complaint_id\nC1\n"
    assert int(os.stat(dest / "branches.csv").st_mtime) == 1_700_000_000


def test_mirror_rerun_copies_nothing_and_drops_files_the_source_lost(tmp_path):
    src, dest = _source(tmp_path / "src"), tmp_path / "raw"
    mirror(str(src), dest, ["branches", "complaints"])
    assert mirror(str(src), dest, ["branches", "complaints"])["copied"] == 0
    extra = dest / "complaints" / "old.csv"
    extra.write_text("complaint_id\nC0\n", encoding="utf-8")
    assert mirror(str(src), dest, ["branches", "complaints"])["removed"] == 1
    assert not extra.exists()


def test_mirror_fails_when_a_table_has_no_file(tmp_path):
    src = _source(tmp_path / "src")
    with pytest.raises(ValueError, match="no files for transactions"):
        mirror(str(src), tmp_path / "raw", ["branches", "transactions"])


def test_mirror_reads_the_source_from_the_environment(tmp_path, monkeypatch, capsys):
    src = _source(tmp_path / "src")
    monkeypatch.setenv("LATAM_BANK_S3_URI", str(src))
    code = mirror_main(["--dest", str(tmp_path / "raw"), "--tables", "branches,complaints",
                        "--env-file", str(tmp_path / "missing.env")])
    assert code == 0 and (tmp_path / "raw" / "branches.csv").exists()
    monkeypatch.delenv("LATAM_BANK_S3_URI")
    assert mirror_main(["--dest", str(tmp_path / "raw2"), "--env-file", str(tmp_path / "missing.env")]) == 1
    assert "LATAM_BANK_S3_URI" in capsys.readouterr().err


def _report(run_id, tables, full_refresh=False, label=None):
    sections = {name: {"files": {"discovered": files}, "warnings_by_reason": {"orphan_fk": warn},
                       "rows": {"bronze_in": rows, "quarantined": quar, "exact_duplicates": 0, "silver_total": rows}}
                for name, (files, rows, quar, warn) in tables.items()}
    return {"run_id": run_id, "started_at": "2026-09-25T22:00:00+00:00", "finished_at": "2026-09-25T22:10:00+00:00",
            "full_refresh": full_refresh, "source_kind": "local", "source_label": label, "tables": sections,
            "totals": {"bronze_in": sum(t[1] for t in tables.values()),
                       "quarantined": sum(t[2] for t in tables.values())}}


def test_load_summary_takes_each_table_from_the_run_that_loaded_it_last():
    first = _report("r1", {"customers": (1, 100, 0, 5), "digital_events": (0, 0, 0, 0)}, full_refresh=True)
    second = _report("r2", {"digital_events": (3, 900, 0, 0)})
    text = load_summary.render([first, second])
    assert "| customers | `r1` | 100 | 0 | 100 |" in text
    assert "| digital_events | `r2` | 900 | 0 | 900 |" in text
    assert "2 tables, 1,000 rows read" in text
    assert "0 (none in the source then)" in text and "Full refresh" in text and "10.0 minutes" in text


def test_load_summary_refuses_fixture_reports():
    with pytest.raises(ValueError, match="synthetic fixture"):
        load_summary.render([_report("r1", {"customers": (1, 10, 0, 0)}, label="SYNTHETIC TEST FIXTURE")])

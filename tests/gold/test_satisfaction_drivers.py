"""What satisfaction depends on (`python -m data_analytics.satisfaction`) on the fixture warehouse: it runs
read-only, its counts match silver, and it never writes fixture numbers over the committed report."""

from data_analytics import satisfaction

from .conftest import query


def test_report_runs_on_the_fixture_and_counts_match_silver(gold_db, tmp_path):
    _, db = gold_db
    out = tmp_path / "satisfaction.md"
    assert satisfaction.main(["--warehouse", str(db), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    csat = query(db, "SELECT count(*) FROM silver.satisfaction_surveys s "
                     "JOIN silver.call_center_interactions i USING (interaction_id) "
                     "JOIN silver.customers c ON c.customer_id = i.customer_id WHERE s.survey_type = 'CSAT'")[0][0]
    assert f"on {csat:,} CSAT surveys" in text
    for section in ("## Answer", "## 1.", "## 2.", "## 3.", "## 4."):
        assert section in text


def test_refuses_to_write_fixture_numbers_into_the_committed_report(gold_db, tmp_path, monkeypatch):
    _, db = gold_db
    committed = tmp_path / "satisfaction.md"
    monkeypatch.setattr(satisfaction, "REPORT_PATH", committed.resolve())
    assert satisfaction.main(["--warehouse", str(db), "--out", str(committed)]) == 1
    assert not committed.exists()

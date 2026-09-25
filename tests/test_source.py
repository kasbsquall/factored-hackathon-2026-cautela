"""Source handling without network: URI parsing, S3 secret construction, file discovery, drift, CSV input."""

from pathlib import Path

import duckdb
import pytest

from data_engineering.pipelines.bronze import detect_drift
from data_engineering.pipelines.run import run_pipeline
from data_engineering.pipelines.source import (
    describe_s3_auth,
    list_files,
    parse_source,
    s3_secret_sql,
)

from .conftest import scalar


@pytest.mark.parametrize("uri, bucket, prefix, root", [
    ("s3://latam-bank/datathon/v1", "latam-bank", "datathon/v1", "s3://latam-bank/datathon/v1"),
    ("s3://latam-bank/datathon/v1/", "latam-bank", "datathon/v1", "s3://latam-bank/datathon/v1"),
    ("S3://latam-bank", "latam-bank", "", "s3://latam-bank"),
    ("s3://my.bucket-01//raw//", "my.bucket-01", "raw", "s3://my.bucket-01/raw"),
])
def test_parse_s3_uri(uri, bucket, prefix, root):
    loc = parse_source(uri)
    assert (loc.kind, loc.bucket, loc.prefix, loc.root) == ("s3", bucket, prefix, root)


@pytest.mark.parametrize("uri, message", [
    ("s3://AKIA:secret@bucket/data", "credentials"),
    ("s3://user@bucket/data", "credentials"),
    ("s3://", "bucket"),
    ("s3:///data", "bucket"),
    ("s3://Bad_Bucket/data", "bucket"),
    ("s3://bucket/data?versionId=1", "query"),
    ("gs://bucket/data", "unsupported scheme"),
    ("https://example.com/data.parquet", "unsupported scheme"),
    ("   ", "empty"),
])
def test_parse_rejects_bad_uris(uri, message):
    with pytest.raises(ValueError, match=message):
        parse_source(uri)


def test_parse_local_path_is_absolute_posix(tmp_path):
    loc = parse_source(str(tmp_path) + "/")
    assert loc.kind == "local" and loc.root == tmp_path.resolve().as_posix()
    assert loc.relative(str(tmp_path / "customers" / "a.parquet")) == "customers/a.parquet"


def test_table_patterns_cover_folder_and_single_file():
    loc = parse_source("s3://bucket/raw")
    patterns = [p for p, _ in loc.patterns("transactions")]
    assert "s3://bucket/raw/transactions/**/*.parquet" in patterns
    assert "s3://bucket/raw/transactions.csv" in patterns


def test_secret_sql_with_static_keys_escapes_values():
    env = {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE", "AWS_SECRET_ACCESS_KEY": "abc'def", "AWS_SESSION_TOKEN": "tok",
           "AWS_REGION": "us-east-1"}
    sql = s3_secret_sql(env)
    assert "PROVIDER config" in sql and "KEY_ID 'AKIAEXAMPLE'" in sql
    assert "SECRET 'abc''def'" in sql and "SESSION_TOKEN 'tok'" in sql and "REGION 'us-east-1'" in sql


def test_secret_sql_defaults_to_credential_chain():
    sql = s3_secret_sql({"AWS_DEFAULT_REGION": "sa-east-1"})
    assert "PROVIDER credential_chain" in sql and "REGION 'sa-east-1'" in sql and "KEY_ID" not in sql


def test_secret_sql_custom_endpoint():
    sql = s3_secret_sql({"AWS_ENDPOINT_URL": "http://localhost:9000/"})
    assert "ENDPOINT 'localhost:9000'" in sql and "URL_STYLE 'path'" in sql and "USE_SSL false" in sql


def test_secret_sql_requires_both_key_parts():
    with pytest.raises(ValueError):
        s3_secret_sql({"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE"})


def test_auth_description_never_contains_the_secret():
    env = {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE1234", "AWS_SECRET_ACCESS_KEY": "super-secret-value"}
    summary = describe_s3_auth(env)
    assert "super-secret-value" not in summary and "AKIAEXAMPLE1234" not in summary and "1234" in summary


def test_list_files_finds_nested_and_single_file_layouts(tmp_path):
    (tmp_path / "branches" / "snapshot_date=2026-05-31").mkdir(parents=True)
    duckdb.sql(f"COPY (SELECT 1 AS a) TO '{(tmp_path / 'branches' / 'x.parquet').as_posix()}'")
    duckdb.sql(f"COPY (SELECT 1 AS a) TO '{(tmp_path / 'branches' / 'snapshot_date=2026-05-31' / 'y.parquet').as_posix()}'")
    duckdb.sql(f"COPY (SELECT 1 AS a) TO '{(tmp_path / 'customers.csv').as_posix()}' (HEADER)")
    con = duckdb.connect()
    loc = parse_source(str(tmp_path))
    assert [f.rel_path for f in list_files(con, loc, "branches")] == [
        "branches/snapshot_date=2026-05-31/y.parquet", "branches/x.parquet"]
    assert [(f.rel_path, f.fmt) for f in list_files(con, loc, "customers")] == [("customers.csv", "csv")]
    assert list_files(con, loc, "products") == []


def test_detect_drift_reports_all_kinds(contracts):
    contract = contracts["daily_exchange_rates"]
    full = {c: "UTF8" for c in contract.column_names}
    schemas = {
        "a.parquet": full,
        "b.parquet": {**{c: t for c, t in full.items() if c != "buy_rate"}, "exchange_rate": "DOUBLE", "extra": "UTF8"},
    }
    events = {(e["kind"], e["column"]) for e in detect_drift(contract, schemas)}
    assert events == {("missing_column", "buy_rate"), ("unexpected_column", "extra"), ("type_changed", "exchange_rate")}


def test_csv_source_with_missing_required_column(tmp_path):
    """A CSV delivery that lacks a NOT NULL column: drift is reported and rows are quarantined, no crash."""
    src = tmp_path / "landing"
    src.mkdir()
    header = "branch_id,branch_code,branch_name,branch_type,address,city,state,country,geographic_zone,phone," \
             "opening_time,closing_time,has_atms,has_teller_windows,branch_opening_date"
    rows = ["BR1,S1,Centro,Main,Calle 1,Bogotá,Cundinamarca,Colombia,Urban,+57 1,09:00,17:00,true,true,2010-01-01",
            "BR2,S2,Norte,Express,Calle 2,Cali,Valle,Colombia,Urban,+57 2,09:00,16:00,false,true,2015-05-05"]
    (src / "branches.csv").write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    report = run_pipeline(str(src), tmp_path / "w.duckdb", tables=["branches"], reports_dir=tmp_path / "r")
    section = report["tables"]["branches"]
    assert ("missing_column", "branch_status") in {(e["kind"], e["column"]) for e in section["drift_events"]}
    assert section["quarantine_by_reason"] == {"null_required": 2}
    assert section["rows"]["silver_total"] == 0
    assert scalar(Path(tmp_path / "w.duckdb"), "SELECT count(*) FROM bronze.branches") == 2

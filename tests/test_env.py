"""The .env loader and the S3 settings it feeds. No network access: the real bucket is never contacted."""

import pytest

from data_engineering.pipelines.env import load_env_file, parse_env
from data_engineering.pipelines.run import main, run_pipeline
from data_engineering.pipelines.source import resolve_source, s3_secret_sql


def test_parse_env_formats():
    text = "\n".join([
        "# comment", "", "AWS_REGION=us-east-2", "export AWS_ACCESS_KEY_ID=AKIAEXAMPLE",
        "AWS_SECRET_ACCESS_KEY='with spaces # and hash'", 'LATAM_BANK_S3_URI="s3://bucket/prefix"',
        "EMPTY=", "INLINE=value # trailing comment", "not a variable line",
    ])
    assert parse_env(text) == {
        "AWS_REGION": "us-east-2", "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "with spaces # and hash", "LATAM_BANK_S3_URI": "s3://bucket/prefix",
        "EMPTY": "", "INLINE": "value",
    }


def test_load_env_file_keeps_existing_values_and_returns_names_only(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AWS_REGION=us-east-2\nAWS_SECRET_ACCESS_KEY=from-file\nEMPTY=\n", encoding="utf-8")
    environ = {"AWS_SECRET_ACCESS_KEY": "already-set"}
    loaded = load_env_file(env_file, environ)
    assert loaded == ["AWS_REGION"]
    assert environ == {"AWS_SECRET_ACCESS_KEY": "already-set", "AWS_REGION": "us-east-2"}


def test_missing_env_file_is_fine(tmp_path):
    assert load_env_file(tmp_path / "absent.env", {}) == []


def test_region_defaults_to_us_east_2():
    sql = s3_secret_sql({"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE", "AWS_SECRET_ACCESS_KEY": "x"})
    assert "REGION 'us-east-2'" in sql
    assert "REGION 'eu-west-1'" in s3_secret_sql({"AWS_REGION": "eu-west-1"})


def test_source_resolution_order():
    env = {"LATAM_BANK_S3_URI": "s3://bucket/prefix"}
    assert resolve_source("data/fixture", env) == "data/fixture"
    assert resolve_source(None, env) == "s3://bucket/prefix"
    with pytest.raises(ValueError, match="LATAM_BANK_S3_URI"):
        resolve_source(None, {})


def test_cli_takes_source_from_env_file(tmp_path, fixture_source, monkeypatch, capsys):
    source, _ = fixture_source
    monkeypatch.setenv("LATAM_BANK_S3_URI", "")  # restored after the test; the file value fills it
    env_file = tmp_path / ".env"
    env_file.write_text(f"LATAM_BANK_S3_URI={source.as_posix()}\n", encoding="utf-8")
    code = main(["--target", str(tmp_path / "w.duckdb"), "--tables", "branches", "--env-file", str(env_file),
                 "--reports-dir", str(tmp_path / "r")])
    assert code == 0
    assert "SYNTHETIC TEST FIXTURE" in capsys.readouterr().out


def test_cli_without_any_source_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LATAM_BANK_S3_URI", raising=False)
    assert main(["--target", str(tmp_path / "w.duckdb"), "--env-file", str(tmp_path / "absent.env")]) == 1
    assert "LATAM_BANK_S3_URI" in capsys.readouterr().err


def test_warehouse_refuses_a_second_source(tmp_path, fixture_source):
    source, _ = fixture_source
    other = tmp_path / "other_source"
    (other / "branches").mkdir(parents=True)
    db = tmp_path / "w.duckdb"
    run_pipeline(str(source), db, tables=["branches"], reports_dir=tmp_path / "r")
    with pytest.raises(ValueError, match="another --target"):
        run_pipeline(str(other), db, tables=["branches"], reports_dir=tmp_path / "r")

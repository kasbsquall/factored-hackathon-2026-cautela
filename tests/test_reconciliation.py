"""Behaviors added after the first real-data run: encoding checks, Hive partition folders, the normalization map
and non-cascading orphan checks. Each test builds a tiny CSV landing zone, so no organizer data is needed."""

from pathlib import Path

import pytest

from data_engineering.pipelines.bronze import EncodingError
from data_engineering.pipelines.run import run_pipeline

from .conftest import scalar

BRANCH_HEADER = ("branch_id,branch_code,branch_name,branch_type,address,city,state,country,geographic_zone,phone,"
                 "opening_time,closing_time,has_atms,has_teller_windows,branch_opening_date,branch_status")
FX_HEADER = "date,source_currency,target_currency,exchange_rate"


def branch(branch_id: str, zone: str = "Urban", country: str = "Colombia", kind: str = "Main") -> str:
    return (f"{branch_id},C{branch_id},Sucursal {branch_id},{kind},Calle 1,Bogotá,Cundinamarca,{country},{zone},"
            f"+57 1,09:00,17:00,true,true,2010-01-01,Active")


def write(path: Path, lines: list[str], encoding: str = "utf-8-sig") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(lines) + "\n").encode(encoding))


def run(src: Path, tmp_path: Path, tables: list[str]) -> tuple[dict, Path]:
    db = tmp_path / "w.duckdb"
    return run_pipeline(str(src), db, tables=tables, reports_dir=tmp_path / "reports"), db


# --- encoding -----------------------------------------------------------------------------------------------------

def test_latin1_file_is_detected_and_read_correctly(tmp_path):
    src = tmp_path / "landing"
    write(src / "branches.csv", [BRANCH_HEADER, branch("B1"), branch("B2", country="Perú")], encoding="latin-1")
    report, db = run(src, tmp_path, ["branches"])
    enc = report["tables"]["branches"]["encoding"]
    assert enc["files_by_encoding"] == {"csv:latin-1": 1} and enc["non_utf8_files"] == ["branches.csv"]
    assert scalar(db, "SELECT city FROM silver.branches WHERE branch_id = 'B1'") == "Bogotá"
    assert scalar(db, "SELECT country FROM silver.branches WHERE branch_id = 'B2'") == "Perú"


def test_utf8_with_bom_is_counted(tmp_path):
    src = tmp_path / "landing"
    write(src / "branches.csv", [BRANCH_HEADER, branch("B1")])
    report, db = run(src, tmp_path, ["branches"])
    enc = report["tables"]["branches"]["encoding"]
    assert enc == {"files_by_encoding": {"csv:utf-8": 1}, "files_with_bom": 1, "non_utf8_files": [],
                   "replacement_characters": 0}
    assert scalar(db, "SELECT branch_id FROM silver.branches") == "B1"  # BOM not glued to the first header


def test_replacement_characters_fail_loudly_and_commit_nothing(tmp_path):
    src = tmp_path / "landing"
    write(src / "branches.csv", [BRANCH_HEADER, branch("B1"), branch("B2").replace("Bogotá", "Bogot�")])
    with pytest.raises(EncodingError, match="U\\+FFFD"):
        run(src, tmp_path, ["branches"])
    db = tmp_path / "w.duckdb"
    assert scalar(db, "SELECT count(*) FROM control.file_ledger") == 0
    assert scalar(db, "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'bronze' "
                      "AND table_name = 'branches'") == 0


# --- Hive partition folders ---------------------------------------------------------------------------------------

def test_partition_folders_are_not_drift_and_are_validated(tmp_path):
    src = tmp_path / "landing" / "daily_exchange_rates"
    write(src / "year=2024/month=01/day=05/rates.csv", [FX_HEADER, "2024-01-05,MXN,USD,0.058"])
    write(src / "year=2024/month=01/day=06/rates.csv", [FX_HEADER, "2024-01-09,COP,USD,0.00025"])  # wrong folder
    write(src / "year=2024/month=01/day=07/rates.csv", ["year,month,day," + FX_HEADER, "2024,1,7,2024-01-07,ARS,USD,0.0011"])
    report, db = run(tmp_path / "landing", tmp_path, ["daily_exchange_rates"])
    section = report["tables"]["daily_exchange_rates"]
    # the files omit the optional columns (reported as missing_column); year/month/day are never "unexpected"
    assert [e for e in section["drift_events"] if e["kind"] != "missing_column"] == []
    assert section["files"]["partition_path_keys"] == ["day", "month", "year"]
    assert section["warnings_by_column"] == {"date:partition_path_mismatch": 1}
    assert section["rows"]["silver_total"] == 3  # a mismatch is reported, the row is kept
    bronze_cols = scalar(db, "SELECT list(column_name) FROM information_schema.columns "
                             "WHERE table_schema = 'bronze' AND table_name = 'daily_exchange_rates'")
    assert "year" in bronze_cols  # carried as data by the third file, kept raw in bronze


def test_hive_folders_do_not_become_columns(tmp_path):
    src = tmp_path / "landing" / "daily_exchange_rates"
    write(src / "year=2024/month=01/day=05/rates.csv", [FX_HEADER, "2024-01-05,MXN,USD,0.058"])
    _, db = run(tmp_path / "landing", tmp_path, ["daily_exchange_rates"])
    cols = scalar(db, "SELECT list(column_name) FROM information_schema.columns WHERE table_schema = 'bronze' "
                      "AND table_name = 'daily_exchange_rates'")
    assert not {"year", "month", "day"} & set(cols)


# --- normalization map --------------------------------------------------------------------------------------------

def test_normalization_maps_documented_variants_and_keeps_raw(tmp_path):
    src = tmp_path / "landing"
    write(src / "branches.csv", [BRANCH_HEADER, branch("B1", zone="Urbana", country="México"),
                                 branch("B2", zone="Urban"), branch("B3", zone="Rurala")])
    report, db = run(src, tmp_path, ["branches"])
    section = report["tables"]["branches"]
    assert scalar(db, "SELECT geographic_zone || '|' || country FROM silver.branches WHERE branch_id = 'B1'") \
        == "Urban|Mexico"
    assert scalar(db, "SELECT _normalized FROM silver.branches WHERE branch_id = 'B1'") \
        == ["country:México", "geographic_zone:Urbana"]
    assert scalar(db, "SELECT _normalized FROM silver.branches WHERE branch_id = 'B2'") == []
    assert scalar(db, "SELECT geographic_zone FROM bronze.branches WHERE branch_id = 'B1'") == "Urbana"
    # an unmapped variant is not widened silently: it still fails the value list
    assert section["quarantine_by_column"] == {"geographic_zone:bad_enum": 1}
    assert section["normalized_values"] == {"country:México": 1, "geographic_zone:Urbana": 1}


# --- orphans do not cascade from quarantined parents --------------------------------------------------------------

PRODUCT_HEADER = ("product_id,customer_id,product_type,product_number,currency,current_balance,opening_date,"
                  "opening_branch_id,product_status,opening_channel,has_linked_app,last_updated")


def product(product_id: str, branch_id: str) -> str:
    return (f"{product_id},C1,Tarjeta Crédito,N{product_id},USD,10.00,2020-01-01,{branch_id},Active,App,true,"
            f"2024-01-01 00:00:00")


def test_child_of_quarantined_parent_is_kept_and_reported_separately(tmp_path):
    src = tmp_path / "landing"
    write(src / "branches.csv", [BRANCH_HEADER, branch("B1"), branch("B2", kind="Gigante")])  # B2: bad enum
    write(src / "products.csv", [PRODUCT_HEADER, product("P1", "B1"), product("P2", "B2"), product("P3", "B9")])
    report, db = run(src, tmp_path, ["branches", "products"])
    products = report["tables"]["products"]
    assert report["tables"]["branches"]["quarantine_by_column"] == {"branch_type:bad_enum": 1}
    assert products["quarantine_by_column"] == {"opening_branch_id:orphan_fk": 1}  # only B9, never delivered
    assert products["warnings_by_column"]["opening_branch_id:parent_quarantined"] == 1
    fk = products["orphan_rate"]["opening_branch_id"]
    assert (fk["orphans"], fk["parent_quarantined"], fk["checked"]) == (1, 1, 3)
    assert scalar(db, "SELECT list(product_id ORDER BY product_id) FROM silver.products") == ["P1", "P2"]
    assert scalar(db, "SELECT product_type FROM silver.products WHERE product_id = 'P1'") == "Credit Card"
    assert products["orphan_rate"]["customer_id"] == {"parent": "customers.customer_id",
                                                     "skipped": "parent table not loaded"}

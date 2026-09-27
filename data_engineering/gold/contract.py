"""Pydantic models and loader for the gold contracts (`contracts/<table>.yaml`, SQL in `sql/<table>.sql`).

A gold contract states the grain (primary key), the typed columns, which silver tables feed the table, how to find
the keys touched by a silver load (row tables only) and the reconciliation queries that tie the table back to
silver. The build refuses a SQL result whose columns differ from the contract.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_engineering.contracts.loader import Classification

GOLD_DIR = Path(__file__).resolve().parent
CONTRACTS_DIR = GOLD_DIR / "contracts"
SQL_DIR = GOLD_DIR / "sql"

_TYPE_PATTERN = re.compile(r"^(VARCHAR|VARCHAR\[\]|BIGINT|INTEGER|DOUBLE|DATE|TIMESTAMP|BOOLEAN|DECIMAL\(\d+,\d+\))$")
_NUMERIC = ("BIGINT", "INTEGER", "DOUBLE", "DECIMAL")

# Added by the build to every materialized row; _source_key and _source_run_ids come from the SQL.
LINEAGE_COLUMNS = {"_source_table": "VARCHAR", "_source_key": "VARCHAR", "_source_run_ids": "VARCHAR[]",
                   "_gold_run_id": "VARCHAR"}


class GoldColumn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    type: str
    classification: Classification
    nullable: bool = True
    allowed_values: list[str] | None = None
    min: float | None = None
    max: float | None = None
    description: str

    @model_validator(mode="after")
    def _validate(self) -> GoldColumn:
        if not _TYPE_PATTERN.match(self.type):
            raise ValueError(f"{self.name}: unsupported gold type {self.type!r}")
        if (self.min is not None or self.max is not None) and not self.type.startswith(_NUMERIC):
            raise ValueError(f"{self.name}: min/max only apply to numeric columns")
        if self.name.startswith("_"):
            raise ValueError(f"{self.name}: names starting with _ are reserved for lineage")
        return self


class Reconciliation(BaseModel):
    """A query returning one number that must be 0 (a count of violations or a difference with silver)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    sql: str
    description: str


class GoldContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str
    kind: Literal["row", "aggregate", "view"]
    purpose: Literal["serving", "analytics"]
    description: str
    primary_key: list[str] = Field(min_length=1)
    sources: list[str] = Field(min_length=1)
    lineage_source: str
    # Row tables: per source table, a SELECT returning the gold keys touched since the watermark `{wm}`.
    changed_keys: dict[str, str] = Field(default_factory=dict)
    columns: list[GoldColumn]
    reconciliation: list[Reconciliation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> GoldContract:
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.table}: duplicate column names")
        missing = [k for k in self.primary_key if k not in names]
        if missing:
            raise ValueError(f"{self.table}: primary key columns not declared: {missing}")
        if any(c.nullable for c in self.columns if c.name in self.primary_key):
            raise ValueError(f"{self.table}: primary key columns must be NOT NULL")
        if self.kind == "row":
            if len(self.primary_key) != 1:
                raise ValueError(f"{self.table}: row tables need a single-column key for incremental merges")
            if set(self.changed_keys) != set(self.sources):
                raise ValueError(f"{self.table}: changed_keys must cover exactly the sources {self.sources}")
            if any("{wm}" not in sql for sql in self.changed_keys.values()):
                raise ValueError(f"{self.table}: every changed_keys query must filter on {{wm}}")
        elif self.changed_keys:
            raise ValueError(f"{self.table}: changed_keys only apply to row tables")
        return self

    @property
    def sql(self) -> str:
        return (SQL_DIR / f"{self.table}.sql").read_text(encoding="utf-8")

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def materialized(self) -> bool:
        return self.kind != "view"

    @property
    def definition_hash(self) -> str:
        """Changes when the contract or its SQL changes; a changed definition forces a full rebuild. The column
        classification is governance metadata that does not change a row, so it is left out."""
        payload = self.model_dump_json(exclude={"columns": {"__all__": {"classification"}}}) + self.sql
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_gold_contracts(directory: Path = CONTRACTS_DIR) -> dict[str, GoldContract]:
    contracts = {}
    for path in sorted(directory.glob("*.yaml")):
        contract = GoldContract.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if contract.table != path.stem:
            raise ValueError(f"{path.name}: table name {contract.table!r} does not match the file name")
        if not (SQL_DIR / f"{contract.table}.sql").exists():
            raise ValueError(f"{contract.table}: missing sql/{contract.table}.sql")
        contracts[contract.table] = contract
    return contracts


def build_order(contracts: dict[str, GoldContract]) -> list[str]:
    """Gold tables that read other gold tables (sources prefixed `gold.`) come after them."""
    order: list[str] = []
    pending = dict(contracts)
    while pending:
        ready = [n for n, c in pending.items()
                 if all(s.removeprefix("gold.") in order for s in c.sources if s.startswith("gold."))]
        if not ready:
            raise ValueError(f"gold dependency cycle among {sorted(pending)}")
        for name in sorted(ready):
            order.append(name)
            del pending[name]
    return order

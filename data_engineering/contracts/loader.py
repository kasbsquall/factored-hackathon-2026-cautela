"""Pydantic models and loader for the per-table data contracts (YAML files in this folder).

A contract is the single place where the pipeline learns what a table should look like. The YAML files are
derived from the organizer data dictionary; every value that had to be reconstructed from a garbled extraction
carries a `provenance` and a `note` so the reader can tell what is read and what is inferred.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACTS_DIR = Path(__file__).resolve().parent

Severity = Literal["error", "warn"]
CheckName = Literal["not_null", "bad_enum", "out_of_range", "orphan_fk"]
# Data classification of a column (README "Data at rest"): pii_direct identifies a person on its own or is free text a
# person wrote or said; pii_quasi identifies a person only in combination, or links a row to one (person keys, date of
# birth, residence, device and location data); sensitive_financial is an account or card number, balance, limit,
# income, credit or fraud score, or a money amount of a person; none is everything else.
Classification = Literal["pii_direct", "pii_quasi", "sensitive_financial", "none"]

_TYPE_PATTERN = re.compile(r"^(VARCHAR\(\d+\)|TEXT|DATE|TIMESTAMP|TIME|BOOLEAN|INTEGER|DECIMAL\(\d+,\d+\))$")
_NUMERIC_PREFIXES = ("INTEGER", "DECIMAL")
_FK_PATTERN = re.compile(r"^[a-z_]+\.[a-z_]+$")


class ColumnContract(BaseModel):
    """One column. `severity` applies to every check on the column unless `check_severity` overrides it.

    Two checks ignore severity on purpose: a null primary key and a value that cannot be cast to the declared type
    always quarantine the row, because the pipeline cannot key or store such a row faithfully.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    type: str
    nullable: bool
    classification: Classification
    pk: bool = False
    unique: bool = False
    fk: str | None = None
    allowed_values: list[str] | None = None
    enum_status: Literal["none", "listed", "decoded", "unknown"] = "none"
    # Reconciliation with the delivered data (see the README reconciliation table). `observed_values` are accepted
    # values the dictionary does not list; `normalize` maps a raw variant (accented or translated) of a value to
    # its canonical form before any check runs. The raw value is kept in bronze and in silver._normalized.
    observed_values: list[str] | None = None
    normalize: dict[str, str] = Field(default_factory=dict)
    min: float | None = None
    max: float | None = None
    severity: Severity
    check_severity: dict[CheckName, Severity] = Field(default_factory=dict)
    provenance: Literal["dictionary", "realigned", "decoded"] = "dictionary"
    profile: bool = False
    description: str
    note: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> ColumnContract:
        if not _TYPE_PATTERN.match(self.type):
            raise ValueError(f"{self.name}: unsupported type {self.type!r}")
        if self.pk and (self.nullable or self.severity != "error"):
            raise ValueError(f"{self.name}: a primary key column must be NOT NULL with severity error")
        if self.fk is not None and not _FK_PATTERN.match(self.fk):
            raise ValueError(f"{self.name}: fk must look like table.column, got {self.fk!r}")
        has_values = bool(self.allowed_values)
        if self.enum_status in ("listed", "decoded") and not has_values:
            raise ValueError(f"{self.name}: enum_status {self.enum_status} requires allowed_values")
        if self.enum_status in ("none", "unknown") and self.allowed_values is not None:
            raise ValueError(f"{self.name}: allowed_values given but enum_status is {self.enum_status}")
        if self.enum_status in ("unknown", "decoded") and not self.note:
            raise ValueError(f"{self.name}: enum_status {self.enum_status} requires a note explaining why")
        if self.enum_status == "unknown" and not self.profile:
            raise ValueError(f"{self.name}: an unknown enum must be profiled (profile: true)")
        if self.observed_values and not (has_values and self.note):
            raise ValueError(f"{self.name}: observed_values extend a value list and need a note explaining why")
        if has_values and self.normalize:
            accepted = set(self.accepted_values)
            outside = sorted(set(self.normalize.values()) - accepted)
            if outside:
                raise ValueError(f"{self.name}: normalize maps to values outside the value list: {outside}")
        if (self.min is not None or self.max is not None) and not self.type.startswith(_NUMERIC_PREFIXES):
            raise ValueError(f"{self.name}: min/max only apply to numeric columns")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"{self.name}: min is greater than max")
        return self

    @property
    def accepted_values(self) -> list[str]:
        return [*(self.allowed_values or []), *(self.observed_values or [])]

    @property
    def duckdb_type(self) -> str:
        return "VARCHAR" if self.type.startswith("VARCHAR") or self.type == "TEXT" else self.type

    def severity_for(self, check: CheckName) -> Severity:
        return self.check_severity.get(check, self.severity)

    @property
    def fk_table(self) -> str | None:
        return self.fk.split(".")[0] if self.fk else None

    @property
    def fk_column(self) -> str | None:
        return self.fk.split(".")[1] if self.fk else None


class TableContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str
    kind: Literal["dimension", "fact", "reference"]
    description: str
    source_system: str
    partitioning: Literal["daily", "monthly_snapshot", "full_snapshot"]
    partition_column: str | None
    event_time_column: str | None
    primary_key: list[str] = Field(min_length=1)
    dedupe_order: list[str]
    expected_rows: int
    layout: Literal["clean", "realigned"]
    notes: list[str] = Field(default_factory=list)
    columns: list[ColumnContract] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate(self) -> TableContract:
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.table}: duplicate column names")
        pk_flagged = [c.name for c in self.columns if c.pk]
        if pk_flagged != self.primary_key:
            raise ValueError(f"{self.table}: primary_key {self.primary_key} does not match pk columns {pk_flagged}")
        for col in [*self.dedupe_order, self.partition_column, self.event_time_column]:
            if col is not None and col not in names:
                raise ValueError(f"{self.table}: unknown column {col!r} in table-level settings")
        if self.partitioning == "daily" and self.partition_column is None:
            raise ValueError(f"{self.table}: daily tables need a partition_column")
        return self

    def column(self, name: str) -> ColumnContract:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(f"{self.table}.{name}")

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def foreign_keys(self) -> list[ColumnContract]:
        return [c for c in self.columns if c.fk]


def load_contract(path: Path) -> TableContract:
    with path.open(encoding="utf-8") as fh:
        contract = TableContract.model_validate(yaml.safe_load(fh))
    if contract.table != path.stem:
        raise ValueError(f"{path.name}: table name {contract.table!r} does not match the file name")
    return contract


def load_contracts(directory: Path = CONTRACTS_DIR) -> dict[str, TableContract]:
    """Load and cross-validate every contract. Foreign keys must point to a key or unique column of a known table."""
    contracts = {c.table: c for c in (load_contract(p) for p in sorted(directory.glob("*.yaml")))}
    for contract in contracts.values():
        for col in contract.foreign_keys:
            parent = contracts.get(col.fk_table)
            if parent is None:
                raise ValueError(f"{contract.table}.{col.name}: unknown parent table {col.fk_table!r}")
            target = parent.column(col.fk_column)
            if parent.primary_key != [target.name] and not target.unique:
                raise ValueError(f"{contract.table}.{col.name}: {col.fk} is not a single-column key")
    return contracts


def dependency_order(contracts: dict[str, TableContract], selected: list[str] | None = None) -> list[str]:
    """Topological order (parents before children) so foreign keys can be checked against loaded parents."""
    wanted = set(selected or contracts)
    unknown = wanted - set(contracts)
    if unknown:
        raise ValueError(f"unknown tables: {sorted(unknown)}")
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in ordered:
            return
        if name in visiting:
            raise ValueError(f"foreign key cycle at {name}")
        visiting.add(name)
        for col in contracts[name].foreign_keys:
            if col.fk_table != name:
                visit(col.fk_table)
        visiting.discard(name)
        ordered.append(name)

    for name in sorted(contracts):
        visit(name)
    return [name for name in ordered if name in wanted]

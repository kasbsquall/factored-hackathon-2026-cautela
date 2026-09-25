"""Issue injection for the synthetic fixture, with exact bookkeeping for the manifest.

Each injected problem touches a distinct row (indices are drawn without replacement from one shuffled pool per
table), so every quarantined row has exactly one reason and the manifest counts are the ground truth the
pipeline tests compare against.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from data_engineering.contracts.loader import TableContract
from data_engineering.fixtures.dimensions import Ctx

DUPLICATE_RATE = 0.02  # dictionary: "~2% across tables"
NULL_RATE = 0.05  # dictionary: "~5% in nullable fields"
LATE_MIN_DAYS, LATE_MAX_DAYS = 3, 10  # late rows land 3-10 days after their event; normal rows within 1 day


@dataclass
class TableLedger:
    exact_duplicates: int = 0
    late_arrival_rows: int = 0
    late_updates: int = 0
    quarantine: Counter = field(default_factory=Counter)
    warnings: Counter = field(default_factory=Counter)
    unique_duplicate_values: dict[str, int] = field(default_factory=dict)
    expected_final: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "exact_duplicates": self.exact_duplicates, "late_arrival_rows": self.late_arrival_rows,
            "late_updates": self.late_updates, "quarantine": dict(sorted(self.quarantine.items())),
            "warnings": dict(sorted(self.warnings.items())),
            "unique_duplicate_values": self.unique_duplicate_values,
            "expected_final": dict(sorted(self.expected_final.items())),
        }


class Injector:
    """Mutates one table's clean rows in place and records what it did."""

    def __init__(self, ctx: Ctx, contract: TableContract, rows: list[dict]):
        self.ctx, self.contract, self.rows = ctx, contract, rows
        self.ledger = TableLedger()
        self._pool = list(range(len(rows)))
        ctx.rng.shuffle(self._pool)
        self._appended: list[dict] = []
        self.error_rows: set[int] = set()  # id() of rows the pipeline must quarantine
        self.quarantined_keys: set[str] = set()  # their primary keys (children then get parent_quarantined)

    def take(self, n: int, where: Callable[[dict], bool] | None = None) -> list[int]:
        picked = [i for i in self._pool if where is None or where(self.rows[i])][:n]
        if len(picked) < n:
            raise ValueError(f"{self.contract.table}: only {len(picked)} rows available, {n} requested")
        chosen = set(picked)
        self._pool = [i for i in self._pool if i not in chosen]
        return picked

    def nulls(self) -> None:
        nullable = [c.name for c in self.contract.columns if c.nullable]
        for row in self.rows:
            for name in nullable:
                if self.ctx.rng.random() < NULL_RATE:
                    row[name] = None

    def set_value(self, reason: str, column: str, value: Any, n: int, where: Callable | None = None) -> None:
        """Overwrite `column` on n rows. The reason is booked as quarantine or warning by the column severity."""
        check = {"null_required": "not_null"}.get(reason, reason)
        always_error = reason == "type_cast_error"  # cast failures quarantine regardless of column severity
        severity = "error" if always_error else self.contract.column(column).severity_for(check)
        for k, i in enumerate(self.take(n, where)):
            self.rows[i][column] = value(k) if callable(value) else value
            if severity == "error":
                self._mark_error(self.rows[i])
        (self.ledger.quarantine if severity == "error" else self.ledger.warnings)[reason] += n

    def null_pk(self, n: int) -> None:
        pk = self.contract.primary_key[0]
        for i in self.take(n):
            copy = {**self.rows[i], pk: None}
            self._appended.append(copy)
            self._mark_error(copy)
        self.ledger.quarantine["null_pk"] += n

    def _mark_error(self, row: dict) -> None:
        self.error_rows.add(id(row))
        key = row[self.contract.primary_key[0]]
        if key is not None and len(self.contract.primary_key) == 1:
            self.quarantined_keys.add(key)

    def count_parent_quarantined(self, rows: list[dict], parents: dict[str, "Injector"]) -> None:
        """Valid rows whose foreign key points at a parent row that the pipeline will quarantine."""
        n = 0
        for col in self.contract.foreign_keys:
            parent = parents.get(col.fk_table)
            if parent is None or not parent.quarantined_keys:
                continue
            n += sum(1 for r in rows if id(r) not in self.error_rows and r.get(col.name) in parent.quarantined_keys)
        if n:
            self.ledger.warnings["parent_quarantined"] += n

    def orphan(self, column: str, n: int) -> None:
        self.set_value("orphan_fk", column, lambda k: f"ORPHAN{self.contract.table[:3].upper()}{k:04d}", n)

    def duplicate_unique(self, column: str, n_values: int) -> None:
        idx = self.take(2 * n_values)
        for a, b in zip(idx[::2], idx[1::2]):
            self.rows[b][column] = self.rows[a][column]
        self.ledger.unique_duplicate_values[column] = n_values

    def late_rows(self, n: int, where: Callable[[dict], bool] | None = None) -> None:
        """Rows whose event happened days before the partition they land in."""
        event_col = self.contract.event_time_column
        for i in self.take(n, where):
            lag = self.ctx.rng.randint(LATE_MIN_DAYS, LATE_MAX_DAYS)
            row = self.rows[i]
            partition = row[self.contract.partition_column]
            row[event_col] = row[event_col].replace(year=partition.year, month=partition.month,
                                                    day=partition.day) - timedelta(days=lag)
        self.ledger.late_arrival_rows += n

    def late_updates(self, n: int, where: Callable[[dict], bool], change: Callable[[dict, Any], dict],
                     track: list[str]) -> None:
        """Later versions of existing rows (same key, new content) that land in a later partition."""
        part_col, pk = self.contract.partition_column, self.contract.primary_key[0]
        latest = self.ctx.end - timedelta(days=LATE_MIN_DAYS)
        for i in self.take(n, lambda r: where(r) and r[part_col] <= latest):
            row = self.rows[i]
            when = min(row[part_col] + timedelta(days=self.ctx.rng.randint(LATE_MIN_DAYS, LATE_MAX_DAYS)), self.ctx.end)
            new = {**change(row, when), part_col: when}
            self._appended.append(new)
            self.ledger.expected_final[new[pk]] = {c: _jsonable(new[c]) for c in track}
        self.ledger.late_updates += n
        if self.contract.event_time_column:
            self.ledger.late_arrival_rows += n

    def duplicates(self) -> None:
        n = round(len(self.rows) * DUPLICATE_RATE)
        for i in self.take(n):
            self._appended.append(dict(self.rows[i]))
        self.ledger.exact_duplicates += n

    def finish(self) -> list[dict]:
        return self.rows + self._appended


def _jsonable(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value

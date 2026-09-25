"""Data access for the tools.

WarehouseRepository reads the silver tables through a read-only DuckDB connection, so no tool can modify source
data even by mistake. CaseStore is a separate sandbox database (a file or in memory) that holds the only writes
the service makes: dispute cases and card blocks. Source rows are never updated; a card's effective status is
the source status overlaid with any sandbox block.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from agent.security.session import DirectoryEntry, normalize_document
from agent.tools.faults import FaultInjector, FaultMode

TX_COLUMNS = ("transaction_id", "transaction_date", "amount", "currency", "amount_usd", "merchant_name", "channel",
              "transaction_type", "transaction_status", "product_id", "customer_id", "transaction_country",
              "transaction_city", "fraud_score", "is_fraud")


def _rows(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


class WarehouseRepository:
    def __init__(self, db_path: str | Path, faults: FaultInjector | None = None) -> None:
        self._con = duckdb.connect(str(db_path), read_only=True)
        self.faults = faults or FaultInjector()

    def close(self) -> None:
        self._con.close()

    def _query(self, sql: str, params: list[Any], faulted: bool = True) -> list[dict[str, Any]]:
        if faulted:
            self.faults.check("warehouse.read")
        return _rows(self._con.cursor().execute(sql, params))

    # ---- identity directory (never faulted: login is a separate service) ------------------------------
    def lookup_by_document(self, document_number: str) -> DirectoryEntry | None:
        rows = self._query(
            "SELECT customer_id, mobile_phone, email, customer_status FROM silver.customers "
            "WHERE upper(regexp_replace(document_number, '[\\s.\\-]', '', 'g')) = ?",
            [normalize_document(document_number)], faulted=False,
        )
        if len(rows) != 1:  # unknown, or a duplicated document the pipeline reported: never guess
            return None
        r = rows[0]
        return DirectoryEntry(r["customer_id"], r["mobile_phone"], r["email"], r["customer_status"])

    # ---- ownership lookups for the permission layer (never faulted) -------------------------------------
    def owner_of(self, kind: str, record_id: str) -> str | None:
        table = {"transaction": ("transactions", "transaction_id"), "product": ("products", "product_id")}.get(kind)
        if table is None:
            return None
        rows = self._query(f"SELECT customer_id FROM silver.{table[0]} WHERE {table[1]} = ?", [record_id],
                           faulted=False)
        return rows[0]["customer_id"] if rows else None

    # ---- tool reads (faulted by default; policy fact lookups pass faulted=False) ---------------
    def get_customer(self, customer_id: str, faulted: bool = True) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM silver.customers WHERE customer_id = ?", [customer_id], faulted)
        return rows[0] if rows else None

    def products_of(self, customer_id: str) -> list[dict[str, Any]]:
        return self._query("SELECT product_id, product_type, product_number, currency, product_status "
                           "FROM silver.products WHERE customer_id = ? ORDER BY product_id", [customer_id])

    def get_product(self, product_id: str, faulted: bool = True) -> dict[str, Any] | None:
        rows = self._query("SELECT product_id, customer_id, product_type, product_status FROM silver.products "
                           "WHERE product_id = ?", [product_id], faulted)
        return rows[0] if rows else None

    def get_transaction(self, transaction_id: str, faulted: bool = True) -> dict[str, Any] | None:
        rows = self._query(
            f"SELECT t.{', t.'.join(TX_COLUMNS)}, p.product_type FROM silver.transactions t "
            "LEFT JOIN silver.products p USING (product_id) WHERE t.transaction_id = ?", [transaction_id], faulted)
        return rows[0] if rows else None

    def transactions_of(self, customer_id: str, start: datetime, end: datetime, limit: int,
                        exclude_types: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        """Own transactions in [start, end], newest first, at most `limit` rows."""
        excluded = list(exclude_types) or ["__none__"]
        return self._query(
            f"SELECT {', '.join(TX_COLUMNS)} FROM silver.transactions WHERE customer_id = ? "
            "AND transaction_date BETWEEN ? AND ? AND transaction_type NOT IN (SELECT unnest(?)) "
            "ORDER BY transaction_date DESC, transaction_id LIMIT ?",
            [customer_id, start.replace(tzinfo=None), end.replace(tzinfo=None), excluded, limit],
        )


_CASE_DDL = """
CREATE SCHEMA IF NOT EXISTS sandbox;
CREATE TABLE IF NOT EXISTS sandbox.dispute_cases (
    case_id VARCHAR PRIMARY KEY, customer_id VARCHAR NOT NULL, transaction_id VARCHAR NOT NULL,
    idempotency_key VARCHAR NOT NULL, args_digest VARCHAR NOT NULL, reason VARCHAR NOT NULL,
    statement_masked VARCHAR, status VARCHAR NOT NULL, policy_rule_ids VARCHAR[], created_at TIMESTAMP NOT NULL,
    trace_id VARCHAR, UNIQUE (customer_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS sandbox.card_blocks (
    block_id VARCHAR PRIMARY KEY, customer_id VARCHAR NOT NULL, product_id VARCHAR NOT NULL UNIQUE,
    reason VARCHAR, created_at TIMESTAMP NOT NULL, trace_id VARCHAR
);
"""


class CaseStore:
    """Sandbox store for the service's own writes. Mock of the bank's case management system."""

    def __init__(self, db_path: str | Path = ":memory:", faults: FaultInjector | None = None) -> None:
        self._con = duckdb.connect(str(db_path))
        self._con.execute(_CASE_DDL)
        self.faults = faults or FaultInjector()

    def close(self) -> None:
        self._con.close()

    def _one(self, sql: str, params: list[Any]) -> dict[str, Any] | None:
        rows = _rows(self._con.cursor().execute(sql, params))
        return rows[0] if rows else None

    def _read_fault(self) -> bool:
        return self.faults.check("case_store.read") is FaultMode.STALE_READ

    # ---- unfaulted lookups used by ownership checks and idempotency -------------------------------------
    def owner_of_case(self, case_id: str) -> str | None:
        row = self._one("SELECT customer_id FROM sandbox.dispute_cases WHERE case_id = ?", [case_id])
        return row["customer_id"] if row else None

    def find_by_idempotency(self, customer_id: str, key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM sandbox.dispute_cases WHERE customer_id = ? AND idempotency_key = ?",
                         [customer_id, key])

    def find_active_case(self, customer_id: str, transaction_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM sandbox.dispute_cases WHERE customer_id = ? AND transaction_id = ? "
                         "ORDER BY created_at LIMIT 1", [customer_id, transaction_id])

    def active_block(self, product_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM sandbox.card_blocks WHERE product_id = ? ORDER BY created_at LIMIT 1",
                         [product_id])

    def _insert(self, table: str, row: dict[str, Any]) -> bool:
        """Insert on a fresh cursor. False when a unique constraint shows a concurrent writer won the race."""
        cols = list(row)
        try:
            self._con.cursor().execute(f"INSERT INTO sandbox.{table} ({', '.join(cols)}) VALUES "
                                       f"({', '.join('?' for _ in cols)})", [row[c] for c in cols])
        except duckdb.ConstraintException:
            return False
        return True

    # ---- faultable operations ---------------------------------------------------------------------------
    def insert_case(self, row: dict[str, Any]) -> dict[str, Any]:
        """Insert unless the idempotency key exists (a retry after a timeout must not duplicate)."""
        mode = self.faults.check("case_store.write")
        existing = self.find_by_idempotency(row["customer_id"], row["idempotency_key"])
        if existing:
            return existing
        row = {**row, "case_id": row.get("case_id") or "CASE-" + secrets.token_hex(6).upper()}
        if mode is not FaultMode.LOST_WRITE and not self._insert("dispute_cases", row):
            return self.find_by_idempotency(row["customer_id"], row["idempotency_key"]) or row
        return row

    def read_case(self, case_id: str) -> dict[str, Any] | None:
        if self._read_fault():
            return None  # a lagging replica: the latest write is not visible yet
        return self._one("SELECT * FROM sandbox.dispute_cases WHERE case_id = ?", [case_id])

    def insert_block(self, row: dict[str, Any]) -> dict[str, Any]:
        mode = self.faults.check("case_store.write")
        existing = self.active_block(row["product_id"])
        if existing:
            return existing
        row = {**row, "block_id": "BLK-" + secrets.token_hex(6).upper()}
        if mode is not FaultMode.LOST_WRITE and not self._insert("card_blocks", row):
            return self.active_block(row["product_id"]) or row
        return row

    def read_block(self, product_id: str) -> dict[str, Any] | None:
        if self._read_fault():
            return None
        return self.active_block(product_id)

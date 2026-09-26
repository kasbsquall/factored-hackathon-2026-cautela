"""Fixtures for the agent layer. They run on the synthetic test fixture warehouse built by tests/conftest.py.

The clock is frozen at 2026-06-01 12:00 UTC, two days after the last fixture transaction. The session secret is a
random test value generated per run; no real secret is read.
"""

from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from agent.clock import FrozenClock
from agent.security.audit import AuditLog
from agent.security.session import IdentityService, MockChannel
from agent.service import ToolService
from agent.tools.faults import FaultInjector
from agent.tools.repository import CaseStore, WarehouseRepository
from data_engineering.gold.run import run_gold

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@dataclass
class Rig:
    service: ToolService
    identity: IdentityService
    repo: WarehouseRepository
    cases: CaseStore
    audit: AuditLog
    clock: FrozenClock
    faults: FaultInjector
    secret: bytes
    sleeps: list[float] = field(default_factory=list)
    _counter: int = 0

    def rid(self) -> str:
        self._counter += 1
        return f"req-{self._counter:05d}"

    def login(self, customer: dict) -> str:
        challenge = self.service.start_login(customer["document_number"])
        code = self.identity.channel.last_code_for(customer["customer_id"])
        return self.service.verify_otp(challenge.challenge_id, code).token

    def call(self, tool: str, args: dict, token: str, confirmation: str | None = None):
        return self.service.execute(tool, args, token, self.rid(), confirmation)

    def confirm_and_call(self, tool: str, args: dict, token: str):
        challenge = self.service.request_confirmation(token, self.rid(), tool, args)
        return self.call(tool, args, token, challenge.token)


def _query(db: Path, sql: str, params: list | None = None) -> list[dict]:
    with duckdb.connect(str(db), read_only=True) as con:
        cur = con.execute(sql, params or [])
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


@pytest.fixture(scope="session")
def warehouse(full_run, tmp_path_factory) -> Path:
    """The fixture warehouse with gold built on a copy: the tools read gold, and the shared silver warehouse other
    tests read is never modified."""
    db = tmp_path_factory.mktemp("agent_gold") / "warehouse.duckdb"
    shutil.copy2(full_run[1], db)
    run_gold(db, reports_dir=db.parent / "reports")
    return db


@pytest.fixture(scope="session")
def people(warehouse) -> dict[str, dict]:
    """Active customers with a unique document, a phone, an active card and approved purchases."""
    rows = _query(warehouse, """
        WITH docs AS (SELECT document_number FROM silver.customers GROUP BY 1 HAVING count(*) = 1),
        cards AS (SELECT customer_id, min(product_id) AS card_id FROM silver.products
                  WHERE product_type IN ('Credit Card', 'Debit Card') AND product_status = 'Active' GROUP BY 1),
        tx AS (SELECT customer_id, count(*) AS n FROM silver.transactions
               WHERE transaction_type = 'Purchase' AND transaction_status = 'Approved' GROUP BY 1)
        SELECT c.customer_id, c.document_number, c.country, c.first_name, c.last_name, cards.card_id, tx.n
        FROM silver.customers c JOIN docs USING (document_number) JOIN cards USING (customer_id)
        JOIN tx USING (customer_id)
        WHERE c.customer_status = 'Active' AND c.mobile_phone IS NOT NULL
        ORDER BY tx.n DESC, c.customer_id
    """)
    by_country = {}
    for row in rows:
        by_country.setdefault(row["country"], row)
    alice = by_country["Mexico"]
    bob = next(r for r in rows if r["customer_id"] != alice["customer_id"])
    return {"alice": alice, "bob": bob, "colombia": by_country["Colombia"], "argentina": by_country["Argentina"]}


@pytest.fixture(scope="session")
def purchases(warehouse, people) -> dict[str, list[dict]]:
    """Approved purchases per test customer, newest first."""
    out = {}
    for key, person in people.items():
        out[key] = _query(warehouse, """
            SELECT transaction_id, transaction_date, amount, currency, amount_usd, merchant_name, fraud_score,
                   is_fraud, channel, product_id
            FROM silver.transactions WHERE customer_id = ? AND transaction_type = 'Purchase'
              AND transaction_status = 'Approved' ORDER BY transaction_date DESC""", [person["customer_id"]])
    return out


@pytest.fixture()
def rig(warehouse, tmp_path) -> Rig:
    secret = secrets.token_bytes(48)
    clock = FrozenClock(NOW)
    faults = FaultInjector()
    repo = WarehouseRepository(warehouse, faults)
    cases = CaseStore(":memory:", faults)
    identity = IdentityService(secret, repo, MockChannel(), clock)
    audit = AuditLog(secret, tmp_path / "audit", clock)
    sleeps: list[float] = []
    service = ToolService(secret, identity, repo, cases, audit, clock, sleep=sleeps.append)
    yield Rig(service, identity, repo, cases, audit, clock, faults, secret, sleeps)
    repo.close()
    cases.close()


@pytest.fixture(scope="session")
def query(warehouse):
    return lambda sql, params=None: _query(warehouse, sql, params)

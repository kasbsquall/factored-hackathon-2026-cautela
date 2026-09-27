"""API settings from environment variables. Values are never logged; only names appear in errors.

  CAUTELA_API_HOST        bind address, default 127.0.0.1 (loopback only)
  CAUTELA_API_PORT        default 8000
  CAUTELA_WAREHOUSE       DuckDB warehouse, default data/warehouse.duckdb (the synthetic fixture)
  CAUTELA_CASES_DB        sandbox case store, default :memory:
  CAUTELA_AUDIT_DIR       audit JSONL directory, default data/audit (git-ignored)
  CAUTELA_AS_OF           ISO date-time the service clock starts at; default: noon the day after the last
                          transaction in the warehouse (the data is static)
  CAUTELA_CORS_ORIGINS    comma-separated allowed origins, default http://localhost:3000
  CAUTELA_DEMO_MODE       off by default. 1 exposes the seeded demo identities and the mock OTP outbox, which shows
                          the one-time code of those identities only (never of another customer in the warehouse)
  CAUTELA_CONSOLE_KEY     key for the human-agent console endpoints (X-Console-Key); generated per process in
                          demo mode when unset and written to data/demo/console_key.txt
  CAUTELA_PROXY_KEY       shared secret of the same-origin frontend proxy (at least 32 characters). A request that
                          carries it in X-Cautela-Proxy-Key may name the visitor address in X-Cautela-Client, and the
                          rate limits key on that address. Unset: the forwarded address is never read
  CAUTELA_RATE_AUTH       requests per window for auth endpoints, "10/60" (10 per 60 s per client address)
  CAUTELA_RATE_TURN       same for conversation endpoints, "30/60"
  CAUTELA_RATE_CONSOLE    same for the read-only console and audit endpoints, "60/60"
  CAUTELA_RATE_TRANSLATE  model calls for translation across all callers, "30/3600"; cached answers do not count
  CAUTELA_TRANSLATE_PER_SESSION  model calls for translation per login session, default 10
  CAUTELA_SEED_FILE       demo identities, default api/seed/demo_customers.json
  SESSION_SECRET, LLM_*   see agent/README.md
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIN_PROXY_KEY_CHARS = 32


class SettingsError(ValueError):
    pass


def _rate(value: str, name: str) -> tuple[int, int]:
    try:
        count, window = (int(x) for x in value.split("/"))
    except ValueError as exc:
        raise SettingsError(f"{name} must look like 10/60") from exc
    if count < 1 or window < 1:
        raise SettingsError(f"{name} must be positive")
    return count, window


@dataclass(frozen=True)
class ApiSettings:
    host: str = "127.0.0.1"
    port: int = 8000
    warehouse: Path = ROOT / "data" / "warehouse.duckdb"
    cases_db: str = ":memory:"
    audit_dir: Path | None = ROOT / "data" / "audit"
    as_of: datetime | None = None
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)
    demo_mode: bool = False
    console_key: str | None = None
    proxy_key: str | None = None
    auth_rate: tuple[int, int] = (10, 60)
    turn_rate: tuple[int, int] = (30, 60)
    console_rate: tuple[int, int] = (60, 60)
    translate_rate: tuple[int, int] = (30, 3600)
    translate_per_session: int = 10
    seed_file: Path = ROOT / "api" / "seed" / "demo_customers.json"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ApiSettings:
        env = os.environ if environ is None else environ
        get = lambda key: (env.get(key) or "").strip()  # noqa: E731
        as_of = None
        if get("CAUTELA_AS_OF"):
            try:
                as_of = datetime.fromisoformat(get("CAUTELA_AS_OF"))
            except ValueError as exc:
                raise SettingsError("CAUTELA_AS_OF must be an ISO date-time") from exc
            as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
        origins = tuple(o.strip() for o in get("CAUTELA_CORS_ORIGINS").split(",") if o.strip())
        proxy_key = get("CAUTELA_PROXY_KEY") or None
        if proxy_key is not None and len(proxy_key) < MIN_PROXY_KEY_CHARS:
            raise SettingsError(f"CAUTELA_PROXY_KEY must be at least {MIN_PROXY_KEY_CHARS} characters")
        try:
            per_session = int(get("CAUTELA_TRANSLATE_PER_SESSION") or cls.translate_per_session)
        except ValueError as exc:
            raise SettingsError("CAUTELA_TRANSLATE_PER_SESSION must be a whole number") from exc
        if per_session < 0:
            raise SettingsError("CAUTELA_TRANSLATE_PER_SESSION must not be negative")
        return cls(
            host=get("CAUTELA_API_HOST") or cls.host,
            port=int(get("CAUTELA_API_PORT") or cls.port),
            warehouse=Path(get("CAUTELA_WAREHOUSE")) if get("CAUTELA_WAREHOUSE") else cls.warehouse,
            cases_db=get("CAUTELA_CASES_DB") or cls.cases_db,
            audit_dir=Path(get("CAUTELA_AUDIT_DIR")) if get("CAUTELA_AUDIT_DIR") else cls.audit_dir,
            as_of=as_of, cors_origins=origins or cls.cors_origins,
            demo_mode=get("CAUTELA_DEMO_MODE").lower() in {"1", "true", "yes"},
            console_key=get("CAUTELA_CONSOLE_KEY") or None,
            proxy_key=proxy_key,
            auth_rate=_rate(get("CAUTELA_RATE_AUTH") or "10/60", "CAUTELA_RATE_AUTH"),
            turn_rate=_rate(get("CAUTELA_RATE_TURN") or "30/60", "CAUTELA_RATE_TURN"),
            console_rate=_rate(get("CAUTELA_RATE_CONSOLE") or "60/60", "CAUTELA_RATE_CONSOLE"),
            translate_rate=_rate(get("CAUTELA_RATE_TRANSLATE") or "30/3600", "CAUTELA_RATE_TRANSLATE"),
            translate_per_session=per_session,
            seed_file=Path(get("CAUTELA_SEED_FILE")) if get("CAUTELA_SEED_FILE") else cls.seed_file,
        )

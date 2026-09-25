"""Quality report: one JSON file per run at <reports_dir>/quality_<run_id>.json."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def _default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def table_section(files: dict, rows: dict, metrics: dict, drift: list[dict], uniques: dict, hwm: dict,
                  encoding: dict | None = None) -> dict:
    valid = rows.get("valid", 0)
    return {
        "files": files,
        "rows": rows,
        "duplicate_rate": round(rows.get("exact_duplicates", 0) / valid, 4) if valid else 0.0,
        "null_rate": metrics.get("null_rate", {}),
        "orphan_rate": metrics.get("orphan_rate", {}),
        "quarantine_by_reason": metrics.get("quarantine_by_reason", {}),
        "quarantine_by_column": metrics.get("quarantine_by_column", {}),
        "warnings_by_reason": metrics.get("warnings_by_reason", {}),
        "warnings_by_column": metrics.get("warnings_by_column", {}),
        "late_arrivals": metrics.get("late_arrivals"),
        "unique_violations": uniques,
        "drift_events": drift,
        "normalized_values": metrics.get("normalized_values", {}),
        "encoding": encoding or {},
        "profiles": metrics.get("profiles", {}),
        "high_water_mark": hwm,
    }


def totals(tables: dict[str, dict]) -> dict:
    keys = ("bronze_in", "quarantined", "valid", "exact_duplicates", "inserted", "updated", "silver_total")
    out = {k: sum(t["rows"].get(k, 0) for t in tables.values()) for k in keys}
    out["files_new"] = sum(t["files"]["new"] for t in tables.values())
    out["drift_events"] = sum(len(t["drift_events"]) for t in tables.values())
    return out


def write_report(path: Path, report: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=_default), encoding="utf-8")
    return path

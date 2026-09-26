"""Markdown report of the update test (data_engineering/reports/freshness_backup_vs_current.md).

Every number comes from the results of one run of data_engineering.freshness.demo. The report holds aggregates only:
counts, dates and byte sizes, never a row of organizer data.
"""

from __future__ import annotations


def _n(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _mb(value: int | None) -> str:
    return "-" if value is None else f"{value / 1e6:,.1f}"


def _range(days: list[str] | None) -> str:
    return f"{days[0]} to {days[1]}" if days else "single file"


def _table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(_n(c) if not isinstance(c, str) else c for c in row) + " |" for row in rows]
    return out + [""]


def _states(r: dict) -> list[str]:
    rows = []
    for t, f in r["files"].items():
        rows.append([t, f["files_before"], f["files_after"], f["identical_content"], f["changed_content"],
                     f["only_after"], f["only_before"], _range(f["days_before"]), _range(f["days_after"]),
                     _mb(f["bytes_before"]), _mb(f["bytes_after"])])
    for t in r.get("listed_only", []):
        b, a = r["listings"]["before"][t], r["listings"]["after"][t]
        rows.append([f"{t} (listing only)", b["files"], a["files"], "not read", "not read", "-", "-",
                     _range(b["days"]), _range(a["days"]), _mb(b["bytes"]), _mb(a["bytes"])])
    return _table(["table", "files before", "files after", "same bytes", "different bytes", "only after",
                   "only before", "partitions before", "partitions after", "MB before", "MB after"], rows)


def _keys(r: dict) -> list[str]:
    rows = []
    for t, k in r["key_changes"].items():
        if k is None:
            rows.append([t, "-", "-", "-", "-", "-", "-", "table absent before or after"])
            continue
        top = sorted(k["changed_columns"].items(), key=lambda kv: -kv[1])[:4]
        cols = ", ".join(f"{c} {n:,}" for c, n in top) or "-"
        rows.append([t, k["keys_before"], k["keys_after"], k["shared_identical"], k["shared_changed"],
                     k["withdrawn"], k["new"], cols])
    return _table(["table", "keys before", "keys after", "shared, same content", "shared, changed", "withdrawn",
                   "new", "most changed columns (shared keys)"], rows)


def _update(r: dict) -> list[str]:
    rows = []
    for t, s in r["run_after"]["tables"].items():
        f, rw, rt, hwm = s["files"], s["rows"], s["retracted"] or {}, s["high_water_mark"]
        rows.append([t, f["discovered"], f["new"], f["rewritten"], f["already_loaded"], rw["bronze_in"],
                     rt.get("silver_rows", 0), rw["replayed"], rw["quarantined"], rw["silver_total"],
                     f"{_n(hwm['before'])} to {_n(hwm['after'])}", len(s["drift_events"])])
    return _table(["table", "files found", "new", "rewritten", "unchanged, not read", "rows read",
                   "silver rows withdrawn", "replayed", "quarantined", "silver rows after", "high-water mark",
                   "drift events"], rows)


def _drift(r: dict) -> list[str]:
    lines = []
    for label, key in (("earlier state", "run_before"), ("update", "run_after")):
        for t, s in r[key]["tables"].items():
            for e in s["drift_events"]:
                detail = e.get("variants") or f"{e.get('files')} files"
                lines.append(f"- {label}, `{t}`: {e['kind']} `{e['column']}` ({detail})")
    return lines or ["- none"]


def _gold(r: dict) -> list[str]:
    rows = []
    for t in r["gold_after"]:
        b, a, z = r["gold_before"].get(t, {}), r["gold_after"][t], r["gold_rerun"][t]
        rows.append([t, b.get("mode"), b.get("rows_total"), a["mode"], a["rows_total"],
                     f"{a['checks'] - a['checks_failed']}/{a['checks']}", z["mode"]])
    return _table(["gold table", "earlier state: mode", "rows", "after update: mode", "rows", "checks passed",
                   "rerun: mode"], rows)


def _fresh(r: dict) -> list[str]:
    b, a = r["freshness_before"], r["freshness_after"]
    rows = []
    for t, x in a["tables"].items():
        y = b["tables"].get(t, {})
        newest_b = y.get("max_partition") or y.get("newest_file_modified")
        newest_a = x.get("max_partition") or x.get("newest_file_modified")
        rows.append([t, x["partitioning"], _n(x["max_lag_days"]), _n(newest_b), _n(y.get("lag_vs_dataset_clock_days")),
                     _n(y.get("status")), _n(newest_a), _n(x.get("lag_vs_dataset_clock_days")), _n(x.get("lag_days")),
                     x["status"]])
    return _table(["table", "cadence", "max lag (days)", "before: newest", "before: days behind clock",
                   "before: status", "after: newest", "after: days behind clock", f"after: days behind {a['as_of']}",
                   "after: status"], rows)


def _equivalence(r: dict) -> list[str]:
    eq = r.get("equivalence")
    if not eq:
        return ["Skipped (--no-baseline).", ""]
    rows = [[k, v.get("rows_left"), v.get("rows_right"), v["equal"]] for k, v in eq.items()]
    equal = sum(1 for v in eq.values() if v["equal"])
    return [f"{equal} of {len(eq)} compared objects are equal.", ""] + _table(
        ["object", "rows, updated warehouse", "rows, one-load warehouse", "equal"], rows)


def render(r: dict) -> str:
    before, after, rerun = r["run_before"]["totals"], r["run_after"]["totals"], r["rerun"]["totals"]
    total_s = sum(r["timings_s"].values())
    gold_rerun_modes = sorted({v["mode"] for v in r["gold_rerun"].values()})
    stale_between = r["freshness_after_rerun_silver"]
    lines = [
        "# Update and freshness test on organizer data: backup vs current",
        "",
        "**Data: organizer data** from the LATAM Bank S3 bucket (`data_backup_20260831/` as the earlier state, `data/` "
        "as the current one). This is not the synthetic test fixture. Only aggregate numbers appear here.",
        "",
        f"Generated by `make freshness-demo` (`uv run python -m data_engineering.freshness.demo`), run started "
        f"{r['started_at'][:19]} UTC, {total_s / 60:,.1f} minutes in total. Tables: {', '.join(r['tables'])}.",
        "",
        "## Method",
        "",
        "1. Both states are copied from the bucket into a git-ignored cache, keeping each object's LastModified as "
        "the file time.",
        "2. A local landing directory receives the earlier state; the bronze/silver pipeline and gold run into a "
        "scratch warehouse.",
        "3. The landing directory is overwritten with the current state, as a source updated in place, and the "
        "pipeline and gold run again on the same warehouse.",
        "4. Pipeline and gold run a third time with nothing changed.",
        "5. A second warehouse is built in one load from the current state, and the updated warehouse is compared "
        "with it table by table, ignoring only run ids and ingestion times.",
        "",
        "## How the two states differ, file by file",
        "",
        *_states(r),
        "## How they differ, row by row (silver, by primary key)",
        "",
        *_keys(r),
        "## The incremental update",
        "",
        f"Earlier state: {_n(before['files_new'])} files, {_n(before['bronze_in'])} rows read, "
        f"{_n(before['quarantined'])} quarantined, {_n(before['silver_total'])} rows in silver.",
        "",
        f"Update: {_n(after['files_new'])} new files and {_n(after['files_rewritten'])} rewritten files read, "
        f"{_n(after['bronze_in'])} rows read, {_n(after['quarantined'])} quarantined, "
        f"{_n(after['silver_total'])} rows in silver.",
        "",
        *_update(r),
        "Drift events reported (they never stop a run):",
        "",
        *_drift(r),
        "",
        "## Gold",
        "",
        *_gold(r),
        "## Freshness before and after",
        "",
        f"The dataset clock is the newest partition of any daily table in the warehouse: "
        f"{r['freshness_before']['dataset_clock']} before the update, {r['freshness_after']['dataset_clock']} after. "
        f"Status applies the policy limits against {r['freshness_after']['as_of']}, the day of the run.",
        "",
        *_fresh(r),
        f"Gold status: {r['freshness_before']['gold']['status']} after the earlier state, "
        f"{r['freshness_after_silver']['gold']['status']} right after the silver update and before gold ran "
        f"(gold older than silver: {_n(r['freshness_after_silver']['gold']['gold_older_than_silver'])}), "
        f"{r['freshness_after']['gold']['status']} after the gold update.",
        "",
        "## Rerun with nothing changed",
        "",
        f"Files read: {_n(rerun['files_new'])} new, {_n(rerun['files_rewritten'])} rewritten; rows read "
        f"{_n(rerun['bronze_in'])}; silver rows inserted {_n(rerun['inserted'])}, updated {_n(rerun['updated'])}. "
        f"Between the silver rerun and the gold rerun, gold was {stale_between['status']} "
        f"(older than silver: {_n(stale_between['gold_older_than_silver'])}). Gold rerun modes: "
        f"{', '.join(gold_rerun_modes)}.",
        "",
        "## Updated warehouse against a single load of the current state",
        "",
        *_equivalence(r),
        "## Runtime",
        "",
        *_table(["step", "seconds"], [[k, f"{v:,.1f}"] for k, v in r["timings_s"].items()]),
    ]
    return "\n".join(lines).rstrip() + "\n"

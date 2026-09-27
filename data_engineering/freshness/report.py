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
    cadence = {t: x["partitioning"] for t, x in r["freshness_after"]["tables"].items()}
    rows = []
    for t, s in r["run_after"]["tables"].items():
        f, rw, rt, hwm = s["files"], s["rows"], s["retracted"] or {}, s["high_water_mark"]
        # a snapshot table's watermark is its file name, which says nothing about time
        mark = f"{_n(hwm['before'])} to {_n(hwm['after'])}" if cadence.get(t) == "daily" else "-"
        rows.append([t, f["discovered"], f["new"], f["rewritten"], f["already_loaded"], f["missing_from_source"],
                     rw["bronze_in"], rt.get("silver_rows", 0), rw["replayed"], rt.get("quarantine_rows", 0),
                     rw["quarantined"], rw["silver_total"], mark, len(s["drift_events"])])
    return _table(["table", "files found", "new", "rewritten", "unchanged, not read", "gone from source",
                   "rows read", "silver rows withdrawn", "replayed", "quarantine rows withdrawn",
                   "quarantined", "silver rows after", "high-water mark", "drift events"], rows)


def _quarantine(r: dict) -> list[str]:
    lines = []
    for label, key in (("Earlier state", "run_before"), ("Update", "run_after")):
        parts = [f"`{t}` {_n(s['rows']['quarantined'])} ({', '.join(f'{k} {v:,}' for k, v in s['quarantine_by_reason'].items())})"
                 for t, s in r[key]["tables"].items() if s["rows"]["quarantined"]]
        lines.append(f"- {label}: " + ("; ".join(parts) if parts else "no row quarantined"))
    withdrawn = sum((s["retracted"] or {}).get("quarantine_rows", 0) for s in r["run_after"]["tables"].values())
    lines.append(f"- Quarantine rows withdrawn with the files they came from: {_n(withdrawn)}")
    return lines + [""]


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


def _meaning(r: dict) -> list[str]:
    files, after = r["files"], r["run_after"]["tables"]
    shared = sum(f["shared_paths"] for f in files.values())
    changed = sum(f["changed_content"] for f in files.values())
    same_bytes_reloaded = sorted(t for t, f in files.items() if f["identical_content"] and after[t]["files"]["rewritten"])
    gone = sum(f["only_before"] for f in files.values())
    eq = r.get("equivalence") or {}
    equal = sum(1 for v in eq.values() if v["equal"])
    replayed = sum(s["rows"]["replayed"] or 0 for s in after.values())
    keys = [k for k in r["key_changes"].values() if k]
    keys_before, withdrawn = sum(k["keys_before"] for k in keys), sum(k["withdrawn"] for k in keys)
    only_now = sorted(t for t, f in files.items() if not f["files_before"] and f["files_after"])
    quarantined_before = r["run_before"]["totals"]["quarantined"]
    lags = {t: x.get("lag_vs_dataset_clock_days") for t, x in r["freshness_before"]["tables"].items()
            if x.get("lag_vs_dataset_clock_days")}
    lag_text = ("; ".join(f"`{t}` was {n:,} days behind the newest daily partition before the update" for t, n in
                          sorted(lags.items())) + ", and 0 after it") if lags else "no daily table lagged the others"
    lines = [
        "`data_backup_20260831/` is an earlier generation of the dataset, not an incremental predecessor of `data/`. "
        f"Of {shared:,} file paths present in both states, {changed:,} hold different bytes; {withdrawn:,} of the "
        f"{keys_before:,} silver primary keys of the earlier state do not exist in the current one; "
        f"{_n(quarantined_before)} rows of the earlier state fail the contracts, which follow the current delivery; "
        f"and {', '.join(only_now) or 'no table'} exist only in the current state. A real daily increment would add "
        "a few partitions and rewrite a few files; this test rewrites almost everything at once.",
        "",
        "What it shows:",
        "",
        f"- Change detection works with the delivery's own file sizes and upload times (S3 LastModified, kept by the "
        f"copy into the landing directory): every file whose size or time changed was read again ({_n(r['run_after']['totals']['files_rewritten'])} rewritten, "
        f"{_n(r['run_after']['totals']['files_new'])} new), and a rerun read nothing.",
        "- Withdrawing a rewritten file's rows works at full volume, including quarantine: rows quarantined from the "
        "earlier generation left with their files.",
        f"- The updated warehouse equals a warehouse loaded once from the current state: {equal} of {len(eq)} "
        "compared objects (bronze, silver, gold, quarantine and the current file ledger), ignoring only run ids and "
        "ingestion times." if eq else "- The one-load comparison was skipped (--no-baseline).",
        "- Gold fails closed: after the rewrite every materialized gold table was rebuilt in full, and gold reported "
        "stale between the silver update and the gold run.",
        f"- The freshness status separates the calendar from the dataset clock: {lag_text}.",
        "",
        "What it does not show:",
        "",
        f"- Replay of an older version from another file: every file of a table was rewritten, so no key had a version "
        f"left elsewhere ({replayed} rows replayed). That path is covered on the synthetic fixture in "
        "`tests/test_freshness.py`.",
        "- A realistic increment (new partitions plus a few corrected files): also covered only on the fixture "
        "(`tests/test_incremental.py`, `tests/test_freshness.py`).",
        f"- Removal of files that disappear from the source: no path existed only in the earlier state ({gone}), "
        "and the pipeline keeps the rows of such files by design.",
        "- Whether either generation is right. Contracts follow the current delivery; values of the earlier "
        "generation outside the contract lists are quarantined or reported, not mapped.",
    ]
    if same_bytes_reloaded:
        lines.append(f"- Content-level change detection: {', '.join(same_bytes_reloaded)} had identical bytes in both "
                     "states but a new upload time, so they were reloaded (the result is the same, the run pays for "
                     "the reload).")
    return lines + [""]


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
        "## What this test shows, and what it does not",
        "",
        *_meaning(r),
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
        "Quarantine:",
        "",
        *_quarantine(r),
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
        f"{', '.join(gold_rerun_modes)}. A silver run that loads nothing still counts as a newer silver run, so "
        "the tool repository would refuse to start until gold runs again: conservative, and cheap because gold then "
        "skips every table.",
        "",
        "## Updated warehouse against a single load of the current state",
        "",
        *_equivalence(r),
        "## Runtime",
        "",
        *_table(["step", "seconds"], [[k, f"{v:,.1f}"] for k, v in r["timings_s"].items()]),
    ]
    return "\n".join(lines).rstrip() + "\n"

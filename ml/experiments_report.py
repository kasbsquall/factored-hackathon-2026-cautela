"""Tracking report: the MLflow runs behind every committed ML result, and the model-selection story.

    uv run python -m ml.experiments_report                   # store -> experiments_runs.json -> experiments.md
    uv run python -m ml.experiments_report --import-reports  # first log the untracked committed JSON as runs

The MLflow store (``mlruns/mlflow.db``, git-ignored) is opened read-only. Its runs (ids, params, latest metrics, git
commit; no artifacts, no local paths) are exported to the committed ``ml/reports/experiments_runs.json``, and
``ml/reports/experiments.md`` is rendered from that snapshot plus the committed report files. Without a local store
the report is rendered from the committed snapshot, so anyone can regenerate it and get the same file.

A run is linked to a committed report only when its logged metrics equal the numbers in that file. Results that no
run holds (the LLM rung, the provider probes, parser read-back) are listed as such. ``--import-reports`` logs the
LLM rung and probe JSON files as runs tagged ``cautela.imported_from``; they are marked as imports everywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from ml import tracking
from ml.data import REPORTS_DIR

STORE = Path("mlruns/mlflow.db")
SNAPSHOT = REPORTS_DIR / "experiments_runs.json"
OUT_MD = REPORTS_DIR / "experiments.md"
IMPORTED = "cautela.imported_from"
KEPT_TAGS = ("mlflow.source.git.commit", "mlflow.source.git.branch")
TRAIN_REPORTS = ("fitted.json", "previous/fitted.json")
EVAL_REPORTS = ("results.json", "results_fresh.json", "previous/results.json", "baselines_val.json",
                "baselines_test.json")
IMPORTABLE = ("results_llm.json", "probe/*.json")
EVAL_KEYS = ("n_cases", "correct_decision_rate.value", "unsafe.count", "top1_accuracy.value", "mrr.value",
             "safe_automated_resolution_rate.value")
LATENCY_KEYS = ("latency_ms.p50", "latency_ms.p95")


# ---------------------------------------------------------------- store and snapshot

def read_store(db: Path) -> dict:
    """Every active run of the store, read through a read-only SQLite connection."""
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        names = dict(con.execute("select experiment_id, name from experiments"))  # deleted ones too: their runs may live
        runs = []
        for rid, name, status, start, eid in con.execute(
                "select run_uuid, name, status, start_time, experiment_id from runs "
                "where lifecycle_stage = 'active' order by start_time, run_uuid").fetchall():
            tags = dict(con.execute("select key, value from tags where run_uuid = ?", (rid,)))
            runs.append({
                "run_id": rid, "experiment": names.get(eid, str(eid)), "name": name, "status": status,
                "start_utc": _utc(start), "git_commit": tags.get(KEPT_TAGS[0], ""), "git_branch": tags.get(KEPT_TAGS[1], ""),
                "tags": {k: v for k, v in sorted(tags.items()) if not k.startswith("mlflow.")},
                "params": dict(sorted(con.execute("select key, value from params where run_uuid = ?", (rid,)))),
                "metrics": dict(sorted(con.execute("select key, value from latest_metrics where run_uuid = ?", (rid,)))),
            })
    finally:
        con.close()
    counts = {n: sum(r["experiment"] == n for r in runs) for n in sorted(names.values())}
    return {"generated_by": "ml/experiments_report.py", "store": "mlruns/mlflow.db (git-ignored, MLflow SQLite store)",
            "experiments": counts, "runs": runs}


def _utc(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def import_reports(store_dir: Path, reports_dir: Path) -> list[str]:
    """Log each untracked committed JSON (LLM rung, probes) once as a run tagged with its path and sha256."""
    import mlflow

    db = store_dir / "mlflow.db"
    done = {r["tags"].get("cautela.source_sha256") for r in read_store(db)["runs"]} if db.exists() else set()
    logged = []
    for path in sorted(p for pattern in IMPORTABLE for p in reports_dir.glob(pattern)):
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha in done:
            continue
        rel = path.relative_to(reports_dir).as_posix()
        body = json.loads(path.read_text(encoding="utf-8"))
        params, metrics = _import_fields(body)
        with tracking.run(f"imported:{rel}", params, tracking_dir=store_dir) as active:
            mlflow.set_tags({IMPORTED: f"ml/reports/{rel}", "cautela.source_sha256": sha,
                             "cautela.note": "imported from a committed JSON report; not a pipeline run"})
            tracking.log_metrics(metrics)
            logged.append(active.info.run_id)
    return logged


def _import_fields(body: dict) -> tuple[dict, dict]:
    keep = ("generated_by", "split", "provider", "model", "reasoning_effort", "prompt_version", "data_version",
            "parallel", "system")
    params = {k: body[k] for k in keep if k in body}
    if "fit" in body:  # LLM rung
        params |= {"train_cases": body["fit"]["train_cases"], "val_cases": body["fit"]["val_cases"],
                   "policy": body["fit"]["decider"]["policy"], "runs": len(body["runs"])}
        return params, {"summary": body["run1_summary"], "variance_mean": body["variance"]["mean"],
                        "fit_cost_usd": body["fit"]["cost_usd"], "spend_usd": body["spend"]["this_script_usd"]}
    return params, {"summary": body["summary"], "baselines_same_cases": body.get("baselines_same_cases", {})}


# ---------------------------------------------------------------- linking runs to committed reports

def _leaves(d: dict) -> dict:
    """Numeric leaves under the metric names ml/tracking.py logs them with."""
    return {k.replace("%", "pct").replace(" ", "_"): float(v) for k, v in tracking.flatten(d).items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def _same(metrics: dict, want: dict) -> bool:
    return all(k in metrics and abs(metrics[k] - v) <= 1e-9 for k, v in want.items())


def match_train(runs: list[dict], report: dict) -> list[str]:
    want = _leaves({"selection": report["ranker_model_selection"], "val_fit": report["val_fit"]})
    return [r["run_id"] for r in runs if r["name"] == "train"
            and r["params"].get("data_version") == report["data_version"]
            and r["params"].get("selected_ranker_model") == report["selected_ranker_model"]
            and _same(r["metrics"], want)]


def match_eval(runs: list[dict], report: dict, system: str) -> dict:
    """Runs with the same headline metrics; ``exact`` also has the same latency (the run that wrote the file)."""
    summary = _leaves({"summary": report["systems"][system]["summary"]})
    head = {f"summary.{k}": summary[f"summary.{k}"] for k in EVAL_KEYS if f"summary.{k}" in summary}
    lat = {f"summary.{k}": summary[f"summary.{k}"] for k in LATENCY_KEYS if f"summary.{k}" in summary}
    same = [r for r in runs if r["params"].get("system") == system and r["params"].get("split") == report["split"]
            and r["params"].get("data_version") == report["data_version"] and _same(r["metrics"], head)]
    return {"runs": [r["run_id"] for r in same], "exact": [r["run_id"] for r in same if _same(r["metrics"], lat)]}


def link(snapshot: dict, reports: dict) -> dict:
    """report path -> linked run ids (train reports: list; evaluate reports: per system)."""
    runs = snapshot["runs"]
    out: dict = {}
    for rel in TRAIN_REPORTS:
        if rel in reports:
            out[rel] = match_train(runs, reports[rel])
    for rel in EVAL_REPORTS:
        if rel in reports:
            out[rel] = {name: match_eval(runs, reports[rel], name) for name in reports[rel]["systems"]}
    for r in runs:
        src = r["tags"].get(IMPORTED, "").removeprefix("ml/reports/")
        if src in reports:
            out.setdefault(src, []).append(r["run_id"])
    return out


def load_reports(reports_dir: Path) -> dict:
    """Every committed JSON report under ``reports_dir`` except the run snapshot itself."""
    return {p.relative_to(reports_dir).as_posix(): json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(reports_dir.rglob("*.json")) if p.name != SNAPSHOT.name}


def runs_by_report(links: dict) -> dict:
    """run id -> committed report files that hold its numbers."""
    out: dict = {}
    for rel, body in links.items():
        ids = body if isinstance(body, list) else [i for v in body.values() for i in v["runs"]]
        for rid in ids:
            out.setdefault(rid, []).append(rel)
    return out


# ---------------------------------------------------------------- entry point

def build(store: Path, reports_dir: Path, snapshot_path: Path) -> tuple[dict, str]:
    """Snapshot from the store when it exists (and write it), else the committed snapshot; then the markdown."""
    from ml.experiments_md import render

    if store.exists():
        snapshot = read_store(store)
        snapshot_path.write_text(json.dumps(snapshot, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                                 encoding="utf-8", newline="\n")
    elif snapshot_path.exists():
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    else:
        raise SystemExit(f"neither {store} nor {snapshot_path} exists")
    return snapshot, render(snapshot, load_reports(reports_dir))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, default=STORE, help="MLflow SQLite store (default %(default)s)")
    ap.add_argument("--import-reports", action="store_true",
                    help="first log results_llm.json and probe/*.json as tagged runs (once per file content)")
    args = ap.parse_args()
    if args.import_reports:
        print(f"imported runs: {import_reports(args.store.parent, REPORTS_DIR)}")
    source = "store" if args.store.exists() else "committed snapshot"
    snapshot, md = build(args.store, REPORTS_DIR, SNAPSHOT)
    OUT_MD.write_text(md, encoding="utf-8", newline="\n")
    print(f"{len(snapshot['runs'])} runs from the {source} -> {SNAPSHOT}, {OUT_MD}")


if __name__ == "__main__":
    main()

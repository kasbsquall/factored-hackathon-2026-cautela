"""ml/experiments_report.py against a tiny MLflow store in a temp directory. No network, no organizer data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ml import tracking
from ml.experiments_md import render
from ml.experiments_report import IMPORTED, build, import_reports, link, load_reports, read_store

VERSION = "v0000000000000001"


def _summary(correct: float, unsafe: int, p50: float) -> dict:
    return {"n_cases": 10, "n_by_label": {"match": 6, "ambiguous": 2, "no_match": 2},
            "top1_accuracy": {"value": 1.0, "ci95": [1.0, 1.0]}, "mrr": {"value": 1.0, "ci95": [1.0, 1.0]},
            "correct_decision_rate": {"value": correct, "ci95": [correct - 0.1, correct]},
            "unsafe_rate": {"value": unsafe / 10, "ci95": [0.0, 0.2]},
            "safe_automated_resolution_rate": {"value": 0.5, "ci95": [0.3, 0.7]},
            "decisions": {"act": 6, "clarify": 2, "abstain": 2},
            "unsafe": {"count": unsafe, "denominator": 10, "acted_denominator": 6},
            "latency_ms": {"p50": p50, "p95": p50 + 1}}


FITTED = {
    "data_version": VERSION, "seed": 13, "features": ["a", "b"], "train_rows": 40, "train_cases": 12, "val_cases": 10,
    "ranker_model_selection": {"logreg_C1.0": {"val_mrr": 1.0, "val_log_loss": 0.03},
                               "hgb_d3_lr0.1": {"val_mrr": 1.0, "val_log_loss": 0.02}},
    "selected_ranker_model": "hgb_d3_lr0.1",
    "systems": {"learned_ranker_disposition": {"model": "multinomial_logreg_C1", "features": ["s1"]}},
    "val_fit": {"learned_ranker_disposition": {"model_selection_val_log_loss": {"multinomial_logreg_C1": 0.04,
                                                                                "hgb_d3": 0.05},
                                               "val_correct_rate": 0.9, "val_unsafe_rate": 0.0, "grid_size": 50}},
    "max_unsafe_rate": 0.01, "act_floor": 0.6,
}
RESULTS = {
    "generated_by": "ml/evaluate.py", "data_version": VERSION, "split": "test", "proposed_system": "sys_a",
    "llm": {"status": "not run", "reason": "--no-llm"},
    "systems": {"sys_a": {"ranker": "learned_x", "decider": "disposition_m",
                          "decider_params": {"policy": {"t_act": 0.8, "t_abstain": 0.1, "k": 3,
                                                        "max_unsafe_rate": 0.01, "fitted_on": "val",
                                                        "act_floor": 0.6}},
                          "summary": _summary(0.9, 1, 2.0)}},
}
PROBE = {"generated_by": "ml/probe_llm.py", "split": "val", "provider": "local", "model": "tiny", "prompt_version": "rank_v1",
         "parallel": 1, "summary": {"cases": 4, "valid_outputs": 4, "errors": [], "top1_match": {"n": 2, "rate": 1.0},
                                    "no_match_median_top_score": 0.1,
                                    "usage": {"cost_usd": 0.0, "latency_ms_p50": 10.0, "latency_ms_p95": 12.0}},
         "baselines_same_cases": {"rules": {"top1_match": {"n": 2, "rate": 1.0}}}}


def _log(store: Path, name: str, params: dict, metrics: dict) -> str:
    with tracking.run(name, params, tracking_dir=store) as active:
        tracking.log_metrics(metrics)
        return active.info.run_id


def _eval_params(split: str = "test", version: str = VERSION) -> dict:
    return {"system": "sys_a", "ranker": "learned_x", "decider": "disposition_m", "data_version": version,
            "split": split, "prompt_version": "n/a"}


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("exp")
    mlruns, reports = root / "mlruns", root / "reports"
    (reports / "probe").mkdir(parents=True)
    for rel, body in (("fitted.json", FITTED), ("results.json", RESULTS), ("probe/local__tiny.json", PROBE)):
        (reports / rel).write_text(json.dumps(body), encoding="utf-8")
    ids = {
        "train": _log(mlruns, "train", {"data_version": VERSION, "selected_ranker_model": "hgb_d3_lr0.1"},
                      {"selection": FITTED["ranker_model_selection"], "val_fit": FITTED["val_fit"]}),
        "earlier": _log(mlruns, "evaluate:sys_a", _eval_params(), {"summary": _summary(0.7, 3, 2.0)}),
        "rerun": _log(mlruns, "evaluate:sys_a", _eval_params(), {"summary": _summary(0.9, 1, 9.0)}),
        "exact": _log(mlruns, "evaluate:sys_a", _eval_params(), {"summary": _summary(0.9, 1, 2.0)}),
        "other_split": _log(mlruns, "evaluate:test_fresh:sys_a", _eval_params("test_fresh"),
                            {"summary": _summary(0.9, 1, 2.0)}),
        "other_version": _log(mlruns, "evaluate:sys_a", _eval_params(version="v2"), {"summary": _summary(0.9, 1, 2.0)}),
    }
    return {"db": mlruns / "mlflow.db", "reports": reports, "ids": ids}


def test_read_store_is_read_only_and_exports_params_metrics_and_git_commit(store):
    before = hashlib.sha256(store["db"].read_bytes()).hexdigest()
    snap = read_store(store["db"])
    assert hashlib.sha256(store["db"].read_bytes()).hexdigest() == before
    assert snap["experiments"][tracking.EXPERIMENT] == 6
    train = next(r for r in snap["runs"] if r["run_id"] == store["ids"]["train"])
    assert train["params"]["selected_ranker_model"] == "hgb_d3_lr0.1"
    assert train["metrics"]["selection.hgb_d3_lr0.1.val_log_loss"] == 0.02
    assert set(train) >= {"start_utc", "git_commit", "status"} and "mlflow.user" not in train["tags"]


def test_links_only_runs_whose_metrics_equal_the_report(store):
    ids = store["ids"]
    links = link(read_store(store["db"]), load_reports(store["reports"]))
    assert links["fitted.json"] == [ids["train"]]
    assert set(links["results.json"]["sys_a"]["runs"]) == {ids["exact"], ids["rerun"]}
    assert links["results.json"]["sys_a"]["exact"] == [ids["exact"]]  # latency tells the writer from the re-run


def test_report_marks_untracked_results_and_earlier_iterations_instead_of_inventing_runs(store):
    md = render(read_store(store["db"]), load_reports(store["reports"]))
    assert "`probe/local__tiny.json` (written by `ml/probe_llm.py`): no run in the local store." in md
    assert f"`{store['ids']['earlier'][:8]}`" in md and "not in a committed report" in md
    assert f"identical re-run: `{store['ids']['rerun'][:8]}`" in md
    assert "\"not run\" (--no-llm)" in md
    assert "90.0% [80.0, 90.0] of 10" in md and "1 of 10; 6 acted" in md  # rates carry their denominators


def test_markdown_is_reproducible_from_the_committed_snapshot_without_a_store(store, tmp_path):
    snap_path = tmp_path / "experiments_runs.json"
    _, from_store = build(store["db"], store["reports"], snap_path)
    _, from_snapshot = build(tmp_path / "absent" / "mlflow.db", store["reports"], snap_path)
    assert from_store == from_snapshot
    assert "C:/" not in snap_path.read_text(encoding="utf-8") and "/tmp" not in snap_path.read_text(encoding="utf-8")


def test_build_without_store_or_snapshot_stops(tmp_path):
    with pytest.raises(SystemExit):
        build(tmp_path / "mlflow.db", tmp_path, tmp_path / "experiments_runs.json")


def test_import_logs_each_committed_json_once_with_a_provenance_tag(tmp_path):
    reports, mlruns = tmp_path / "reports", tmp_path / "mlruns"
    (reports / "probe").mkdir(parents=True)
    (reports / "probe" / "local__tiny.json").write_text(json.dumps(PROBE), encoding="utf-8")
    first = import_reports(mlruns, reports)
    assert len(first) == 1 and import_reports(mlruns, reports) == []
    run = read_store(mlruns / "mlflow.db")["runs"][0]
    assert run["tags"][IMPORTED] == "ml/reports/probe/local__tiny.json"
    assert run["params"]["model"] == "tiny" and run["metrics"]["summary.top1_match.rate"] == 1.0
    md = render(read_store(mlruns / "mlflow.db"), load_reports(reports))
    assert "imported as a tagged run; no pipeline run" in md and "(imported)" in md

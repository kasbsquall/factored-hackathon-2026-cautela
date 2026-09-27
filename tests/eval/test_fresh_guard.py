"""Run-once guard of eval_fresh (eval/fresh/guard.py) and its use by eval/fresh/run.py, with stubbed runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eval.fresh import guard
from eval.fresh import run as fresh_run
from eval.oracle import rules


# ---- markers ---------------------------------------------------------------------------------------------
def test_a_configuration_without_a_marker_may_run(tmp_path):
    guard.check_run_once(["rules", "learned"], override=False, marker_dir=tmp_path,
                         results_path=tmp_path / "results.json")


def test_a_configuration_with_a_marker_is_refused(tmp_path):
    (tmp_path / "rules.json").write_text(json.dumps({"run_id": "rules-x", "finished_at": "t"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="rules"):
        guard.check_run_once(["learned", "rules"], override=False, marker_dir=tmp_path,
                             results_path=tmp_path / "results.json")
    guard.check_run_once(["learned"], override=False, marker_dir=tmp_path, results_path=tmp_path / "results.json")


def test_results_without_a_marker_also_count_as_a_run(tmp_path):
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"configs": {"llm": {"run_id": "llm-x"}}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="llm"):
        guard.check_run_once(["llm"], override=False, marker_dir=tmp_path, results_path=results)


def test_override_skips_the_markers_and_a_partial_run_needs_it(tmp_path):
    (tmp_path / "rules.json").write_text("{}", encoding="utf-8")
    guard.check_run_once(["rules"], override=True, marker_dir=tmp_path)
    with pytest.raises(SystemExit, match="--limit"):
        guard.check_run_once(["learned"], override=False, limit=5, marker_dir=tmp_path,
                             results_path=tmp_path / "results.json")
    guard.check_run_once(["learned"], override=True, limit=5, marker_dir=tmp_path)


def test_override_results_never_go_to_the_reported_file():
    reported = guard.results_path_for(override=False)
    unreported = guard.results_path_for(override=True)
    assert reported == guard.FRESH_RESULTS_PATH
    assert unreported != reported and unreported.parent == guard.FRESH_UNREPORTED_DIR
    assert "data" in unreported.parts  # git-ignored


def test_marker_records_the_run_and_is_never_overwritten(tmp_path):
    manifest = {"suite_sha256": "a" * 64, "gold_sha256": "b" * 64, "suite_version": "v", "policy_version": "p"}
    path = guard.write_marker("rules", {"run_id": "rules-compliant-fresh-1"}, manifest, {"attentive": True},
                              marker_dir=tmp_path)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["config"] == "rules" and body["run_id"] == "rules-compliant-fresh-1"
    assert body["suite_sha256"] == "a" * 64 and body["flags"] == {"attentive": True}
    assert "git_head" in body and "agent_api_ml_uncommitted_changes" in body
    with pytest.raises(SystemExit, match="overwrite"):
        guard.write_marker("rules", {"run_id": "again"}, manifest, {}, marker_dir=tmp_path)


# ---- frozen files ------------------------------------------------------------------------------------------
def _frozen(tmp_path: Path, policy: str | None = None) -> tuple[Path, Path, Path]:
    suite, gold, manifest = tmp_path / "suite.jsonl", tmp_path / "gold.jsonl", tmp_path / "manifest.json"
    suite.write_text('{"conv_id": "a"}\n', encoding="utf-8", newline="\n")
    gold.write_text('{"conv_id": "a", "gold_kind": "resolved"}\n', encoding="utf-8", newline="\n")
    manifest.write_text(json.dumps({
        "suite_sha256": hashlib.sha256(suite.read_bytes()).hexdigest(),
        "gold_sha256": hashlib.sha256(gold.read_bytes()).hexdigest(),
        "policy_version": policy or rules()["version"], "warehouse_slice_content_hash": "x"}), encoding="utf-8")
    return suite, gold, manifest


def test_check_frozen_accepts_the_frozen_files(tmp_path):
    suite, gold, manifest = _frozen(tmp_path)
    assert guard.check_frozen(suite, manifest, gold, slice_path=None)["policy_version"] == rules()["version"]


@pytest.mark.parametrize("tamper", ["suite", "gold"])
def test_check_frozen_refuses_a_changed_suite_or_gold(tmp_path, tamper):
    suite, gold, manifest = _frozen(tmp_path)
    target = suite if tamper == "suite" else gold
    target.write_text(target.read_text(encoding="utf-8").replace("a", "b"), encoding="utf-8")
    with pytest.raises(SystemExit, match="sha256"):
        guard.check_frozen(suite, manifest, gold, slice_path=None)


def test_check_frozen_refuses_a_policy_change(tmp_path):
    suite, gold, manifest = _frozen(tmp_path, policy="1999-01-01.0")
    with pytest.raises(SystemExit, match="rules.yaml"):
        guard.check_frozen(suite, manifest, gold, slice_path=None)


# ---- the runner ----------------------------------------------------------------------------------------------
class _Ledger:
    def status(self):
        return {"refused_calls": 0, "usd_estimated": 0.0}


class _Spend:
    CAP_USD = 3.0

    @staticmethod
    def ledger():
        return _Ledger()

    @staticmethod
    def remaining(_):
        return 3.0


@pytest.fixture
def stubbed(tmp_path, monkeypatch):
    """eval/fresh/run.py with every path in tmp_path and the agent replaced by a stub that records its calls."""
    suite = tmp_path / "suite.jsonl"
    suite.write_text('{"conv_id": "a", "variance_subset": false}\n', encoding="utf-8")
    manifest = {"suite_version": "v", "suite_sha256": "s" * 64, "gold_sha256": "g" * 64, "policy_version": "p",
                "warehouse_slice_content_hash": "h"}
    calls: list[tuple] = []

    def fake_run_config(name, suite_rows, customer, workers, ledger=None, tag="", slice_path=None):
        calls.append((name, customer, tag, slice_path, len(suite_rows)))
        return {"run_id": f"{name}-{customer}{tag}-1", "rows": []}

    monkeypatch.setattr(guard, "FRESH_MARKER_DIR", tmp_path / "ran")
    monkeypatch.setattr(guard, "FRESH_RESULTS_PATH", tmp_path / "results.json")
    monkeypatch.setattr(guard, "FRESH_UNREPORTED_DIR", tmp_path / "unreported")
    monkeypatch.setattr(guard, "check_frozen", lambda: manifest)
    monkeypatch.setattr(guard, "_git", lambda *a: None)
    monkeypatch.setattr(fresh_run, "FRESH_SUITE_PATH", suite)
    monkeypatch.setattr(fresh_run, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(fresh_run, "spend", _Spend)
    monkeypatch.setattr(fresh_run, "run_config", fake_run_config)
    monkeypatch.setattr(fresh_run, "aggregate", lambda run: {"run_id": run["run_id"], "summary": {
        k: {"rate": 0.5} for k in ("correct_outcome", "safe_automated_resolution", "unsafe", "containment")}})
    return tmp_path, calls


def test_first_run_writes_results_and_the_marker_on_the_fresh_slice(stubbed):
    tmp_path, calls = stubbed
    fresh_run.main(["--configs", "rules"])
    assert calls == [("rules", "compliant", "-fresh", fresh_run.FRESH_SLICE_PATH, 1)]
    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert results["reportable"] is True and results["suite"] == "eval_fresh" and "rules" in results["configs"]
    marker = json.loads((tmp_path / "ran" / "rules.json").read_text(encoding="utf-8"))
    assert marker["run_id"] == "rules-compliant-fresh-1"


def test_second_run_of_a_configuration_is_refused_before_anything_runs(stubbed):
    tmp_path, calls = stubbed
    fresh_run.main(["--configs", "rules"])
    calls.clear()
    with pytest.raises(SystemExit, match="once per configuration"):
        fresh_run.main(["--configs", "learned,rules"])
    assert calls == []  # learned did not run either: the check covers every requested configuration first
    fresh_run.main(["--configs", "learned"])
    assert [c[0] for c in calls] == ["learned"]
    assert set(json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["configs"]) == {"rules", "learned"}


def test_override_run_is_not_reported_and_leaves_markers_alone(stubbed):
    tmp_path, _ = stubbed
    fresh_run.main(["--configs", "rules"])
    before = (tmp_path / "results.json").read_text(encoding="utf-8")
    fresh_run.main(["--configs", "rules", "--rerun-not-for-reporting", "--limit", "1"])
    assert (tmp_path / "results.json").read_text(encoding="utf-8") == before
    assert sorted(p.name for p in (tmp_path / "ran").iterdir()) == ["rules.json"]
    unreported = list((tmp_path / "unreported").glob("results-*.json"))
    assert len(unreported) == 1
    assert json.loads(unreported[0].read_text(encoding="utf-8"))["reportable"] is False


def test_partial_run_without_override_is_refused(stubbed):
    _, calls = stubbed
    with pytest.raises(SystemExit, match="--limit"):
        fresh_run.main(["--configs", "rules", "--limit", "10"])
    assert calls == []


def test_unknown_configuration_is_rejected(stubbed):
    with pytest.raises(SystemExit):
        fresh_run.main(["--configs", "gpt"])


def test_policy_files_read_by_the_fresh_builder_are_unchanged_since_the_freeze():
    """The oracle of eval_fresh was built from these files; any byte change, comments included, breaks the freeze."""
    root = Path(__file__).resolve().parents[2]
    frozen = json.loads((root / "eval/fresh/manifest.json").read_text(encoding="utf-8"))["agent_code_read_by_the_builder"]
    for rel, sha in frozen.items():
        data = (root / rel).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == sha, f"{rel} changed after the eval_fresh freeze"

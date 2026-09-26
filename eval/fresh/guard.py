"""The run-once guard of eval_fresh, like the one of `ml.evaluate --split test_fresh`.

eval_fresh is the suite that gives the final, honest number, so each configuration runs on it once. After a
configuration's run, eval/fresh/run.py writes eval/fresh/ran/<config>.json; that marker is committed with the
results, and every later run of the same configuration is refused.

OVERRIDE_FLAG bypasses the marker for debugging only. It is never used for reporting: such a run writes to
data/eval/fresh_unreported/ (git-ignored), never to eval/fresh/results.json, and never writes or changes a marker.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from eval.oracle import rules
from eval.paths import (
    FRESH_GOLD_PATH,
    FRESH_MANIFEST_PATH,
    FRESH_MARKER_DIR,
    FRESH_RESULTS_PATH,
    FRESH_SLICE_PATH,
    FRESH_SUITE_PATH,
    FRESH_UNREPORTED_DIR,
    ROOT,
)
from eval.slice import content_hash

OVERRIDE_FLAG = "--rerun-not-for-reporting"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def check_frozen(suite_path: Path = FRESH_SUITE_PATH, manifest_path: Path = FRESH_MANIFEST_PATH,
                 gold_path: Path = FRESH_GOLD_PATH, slice_path: Path | None = FRESH_SLICE_PATH) -> dict:
    """The suite, its gold and the warehouse slice are exactly the ones frozen in the manifest, under the same
    policy version. `slice_path=None` skips the slice check (tests only)."""
    if not manifest_path.exists():
        raise SystemExit(f"{manifest_path} not found: eval_fresh has not been frozen")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not suite_path.exists():
        raise SystemExit(f"{suite_path} not found: run `uv run python -m eval.fresh.build --verify`")
    if _sha(suite_path) != manifest["suite_sha256"]:
        raise SystemExit(f"{suite_path.name} does not match the sha256 frozen in {manifest_path.name}")
    if not gold_path.exists() or _sha(gold_path) != manifest["gold_sha256"]:
        raise SystemExit(f"{gold_path.name} is missing or does not match the sha256 frozen in {manifest_path.name}")
    current = rules()["version"]
    if current != manifest.get("policy_version"):
        raise SystemExit(f"agent/policy/rules.yaml is version {current}; eval_fresh's gold was set under "
                         f"{manifest.get('policy_version')}. The gold cannot be rebuilt after a run; report the "
                         "policy change instead of running on a suite whose gold no longer describes the policy")
    if slice_path is not None:
        if not slice_path.exists():
            raise SystemExit(f"{slice_path} not found: run `uv run python -m eval.slice --split test_fresh`")
        with duckdb.connect(str(slice_path), read_only=True) as con:
            if content_hash(con) != manifest["warehouse_slice_content_hash"]:
                raise SystemExit(f"{slice_path.name} does not match the slice hash frozen in {manifest_path.name}")
    return manifest


def marker_path(config: str, marker_dir: Path | None = None) -> Path:
    return (marker_dir or FRESH_MARKER_DIR) / f"{config}.json"


def check_run_once(configs: list[str], override: bool, limit: int = 0, marker_dir: Path | None = None,
                   results_path: Path | None = None) -> None:
    """Refuse a configuration that already ran on eval_fresh (a marker, or its results in results.json), and a
    partial run (--limit) unless it is explicitly not for reporting."""
    if override:
        return
    results_path = results_path or FRESH_RESULTS_PATH
    if limit:
        raise SystemExit(f"--limit runs part of eval_fresh, which spends it without a usable number; it is allowed "
                         f"only with {OVERRIDE_FLAG}")
    done = [c for c in configs if marker_path(c, marker_dir).exists()]
    if results_path.exists():
        reported = json.loads(results_path.read_text(encoding="utf-8")).get("configs", {})
        done += [c for c in configs if c in reported and c not in done]
    if done:
        details = []
        for c in done:
            path = marker_path(c, marker_dir)
            if path.exists():
                m = json.loads(path.read_text(encoding="utf-8"))
                details.append(f"{c} (run {m.get('run_id')}, finished {m.get('finished_at')})")
            else:
                details.append(f"{c} (results present in {results_path.name})")
        raise SystemExit("eval_fresh runs once per configuration and these already ran: " + "; ".join(details)
                         + f". {OVERRIDE_FLAG} exists for debugging and is never used for reporting.")


def results_path_for(override: bool, now: datetime | None = None) -> Path:
    """Where a run writes its results: the committed file for the one reporting run, a git-ignored file otherwise."""
    if not override:
        return FRESH_RESULTS_PATH
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")
    return FRESH_UNREPORTED_DIR / f"results-{stamp}.json"


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True, cwd=ROOT).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write_marker(config: str, body: dict, manifest: dict, flags: dict, marker_dir: Path | None = None) -> Path:
    """Record that `config` ran on eval_fresh. Never overwrites an existing marker."""
    path = marker_path(config, marker_dir)
    if path.exists():
        raise SystemExit(f"{path} already exists: refusing to overwrite a run-once marker")
    dirty = _git("status", "--porcelain", "--", "agent", "api", "ml")
    marker = {
        "config": config, "run_id": body.get("run_id"), "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "suite_sha256": manifest["suite_sha256"], "gold_sha256": manifest["gold_sha256"],
        "suite_version": manifest["suite_version"], "policy_version": manifest["policy_version"],
        "git_head": _git("rev-parse", "HEAD"),
        "agent_api_ml_uncommitted_changes": bool(dirty) if dirty is not None else None,
        "flags": flags, "valid": body.get("valid", True),
        "note": "eval_fresh ran once for this configuration. Commit this file with eval/fresh/results.json; "
                "eval/fresh/run.py refuses every later run of this configuration.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path

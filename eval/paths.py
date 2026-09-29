"""Paths shared by the end-to-end evaluation. Everything under data/ is git-ignored (organizer-derived)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "eval"
DISPUTES_DIR = EVAL_DIR / "cases" / "disputes"
ADVERSARIAL_PATH = EVAL_DIR / "cases" / "security" / "adversarial.jsonl"
HELDOUT_DIR = EVAL_DIR / "heldout"
SUITE_PATH = HELDOUT_DIR / "suite.jsonl"          # git-ignored, rebuilt by `python -m eval.build --verify`
MANIFEST_PATH = HELDOUT_DIR / "manifest.json"     # committed: sha256 of the suite and of the warehouse slice
DATASHEET_PATH = HELDOUT_DIR / "DATASHEET.md"     # committed
RESULTS_PATH = EVAL_DIR / "results.json"
REPORT_PATH = EVAL_DIR / "report.md"

# eval_fresh: a second end-to-end suite from the test_fresh cases, frozen before any agent fix and run once per
# configuration (eval/fresh/). Only the manifest, datasheet, gold summary, results and run markers are committed.
FRESH_DIR = EVAL_DIR / "fresh"
FRESH_SUITE_PATH = FRESH_DIR / "suite.jsonl"         # git-ignored, rebuilt by `python -m eval.fresh.build --verify`
FRESH_MANIFEST_PATH = FRESH_DIR / "manifest.json"    # committed
FRESH_DATASHEET_PATH = FRESH_DIR / "DATASHEET.md"    # committed
FRESH_GOLD_PATH = FRESH_DIR / "gold.jsonl"           # committed: gold per conversation, no organizer ids
FRESH_RESULTS_PATH = FRESH_DIR / "results.json"      # written by the one reporting run per configuration
FRESH_MARKER_DIR = FRESH_DIR / "ran"                 # one marker per configuration, committed after its run

# Noisy-customer slice (eval/noisy/): a dev half from the test split, used to fix the agent, and a sealed half from
# test_fresh built by the same frozen generator after the code freeze. suite.jsonl is git-ignored in both.
NOISY_DIR = EVAL_DIR / "noisy"
NOISY_DEV_DIR = NOISY_DIR / "dev"
NOISY_SEALED_DIR = NOISY_DIR / "sealed"

DATA_DIR = ROOT / "data" / "eval"
REAL_WAREHOUSE = ROOT / "data" / "warehouse_real.duckdb"
SLICE_PATH = DATA_DIR / "warehouse_eval.duckdb"
RUNS_DIR = DATA_DIR / "runs"
SPEND_LEDGER = DATA_DIR / "llm_spend.json"
FRESH_SLICE_PATH = DATA_DIR / "warehouse_eval_fresh.duckdb"
FRESH_UNREPORTED_DIR = DATA_DIR / "fresh_unreported"  # override runs land here and are never used for reporting

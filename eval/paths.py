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

DATA_DIR = ROOT / "data" / "eval"
REAL_WAREHOUSE = ROOT / "data" / "warehouse_real.duckdb"
SLICE_PATH = DATA_DIR / "warehouse_eval.duckdb"
RUNS_DIR = DATA_DIR / "runs"
SPEND_LEDGER = DATA_DIR / "llm_spend.json"

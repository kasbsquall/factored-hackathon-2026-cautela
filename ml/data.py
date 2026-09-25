"""Loading evaluation cases and turning them into ranker inputs."""

from __future__ import annotations

import json
from pathlib import Path

CASES_DIR = Path("eval/cases/disputes")
MODELS_DIR = Path("data/ml/models")
REPORTS_DIR = Path("ml/reports")


def load_cases(split: str, cases_dir: Path = CASES_DIR) -> list[dict]:
    with (cases_dir / f"{split}.jsonl").open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def data_version(cases_dir: Path = CASES_DIR) -> str:
    return json.loads((cases_dir / "manifest.json").read_text(encoding="utf-8"))["data_version"]


def ranker_input(case: dict) -> dict:
    """Only what a ranker may see: the text, the report date and the language. No hints, no labels."""
    return {"text": case["description"], "report_date": case["report_date"], "language": case["language"]}


def group_of(case: dict) -> str:
    """Bootstrap and split unit: a Spanish case and its Portuguese twin share one group."""
    return case.get("source_case_id") or case["case_id"]


def record(case: dict, ranked: list[tuple[str, float]]) -> dict:
    return {"case_id": case["case_id"], "split": case["split"], "label": case["label"],
            "target": case["target_transaction_id"], "consistent": case["consistent_ids"], "ranked": ranked,
            "group": group_of(case), "customer_ref": case["customer_ref"]}

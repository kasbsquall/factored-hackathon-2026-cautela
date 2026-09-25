"""Export one JSON document per tool contract to docs/schemas/tools/.

Run: uv run python -m agent.tools.export_schemas
A test fails if the committed files drift from the pydantic models, so the documentation cannot go stale.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent.policy.engine import load_rules
from agent.tools.contracts import ERROR_CODES
from agent.tools.registry import TOOLS

OUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "tools"


def tool_document(name: str) -> dict[str, Any]:
    spec = TOOLS[name]
    confirm = set(load_rules()["confirmations"]["actions"])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"cautela/tools/{name}.json",
        "name": name,
        "kind": spec.kind,
        "description": spec.description,
        "requires_confirmation": name in confirm,
        "ownership_checked_args": spec.owned_args,
        "side_effects": spec.side_effects,
        "errors": {code: ERROR_CODES[code] for code in spec.errors},
        "input": spec.input_model.model_json_schema(),
        "output": spec.output_model.model_json_schema(),
    }


def render(name: str) -> str:
    return json.dumps(tool_document(name), indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def export(out_dir: Path = OUT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in sorted(TOOLS):
        path = out_dir / f"{name}.json"
        path.write_text(render(name), encoding="utf-8", newline="\n")
        paths.append(path)
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    for written in export(parser.parse_args().out):
        print(written)

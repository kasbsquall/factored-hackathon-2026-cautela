"""Export the API contract to docs/schemas/openapi.json for the Next.js frontend (`npm run gen:api`).

    uv run python -m api.export_openapi

The document is built from the route and model definitions only; no warehouse or secret is needed.
tests/api/test_api.py fails if the committed file drifts from the code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from api.app import create_app
from api.settings import ROOT, ApiSettings

OUT = ROOT / "docs" / "schemas" / "openapi.json"


def openapi_document() -> dict[str, Any]:
    return create_app(settings=ApiSettings()).openapi()


def render() -> str:
    return json.dumps(openapi_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    Path(OUT).write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

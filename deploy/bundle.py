"""Build the demo bundle the public image serves: the learned model, a slim warehouse of the demo customers, the seed.

    uv run python -m deploy.bundle                     # make demo-artifacts

Inputs (all git-ignored, on the laptop): the learned model artifacts in data/ml/models (written by `python -m
ml.train`), the full organizer warehouse data/warehouse_real.duckdb, and the seed data/demo/real_seed.json written by
`python -m deploy.demo_select` (the demo customers and the demo clock).

Output:
  deploy/demo-bundle/            git-ignored, never committed (organizer-derived rows and the fitted model)
    models/learned.pkl, models/systems.pkl
    warehouse.duckdb             the demo customers only (data_engineering/slice.py), lineage columns kept
    demo_customers.json          the seed, with the demo clock `as_of`
  deploy/demo-bundle.lock.json   committed: size and sha256 of each file, the demo clock, the model name

Before the lock is written, every seed identity is replayed against the slim warehouse with the bundled model, in
Spanish and Portuguese (deploy/demo_select.py). If any scenario fails, the bundle is removed and nothing is locked.
The warehouse file bytes differ between two builds of the same content (DuckDB layout), so the lock also records a
content digest: a rebuild with the same inputs has the same `warehouse_content_sha256` and a new file hash.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from data_engineering.slice import content_digest, slice_warehouse
from deploy import lock
from deploy.demo_select import load_learned, validate_seed

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "deploy" / "demo-bundle"
LOCK = ROOT / "deploy" / "demo-bundle.lock.json"
SOURCE = ROOT / "data" / "warehouse_real.duckdb"
SEED = ROOT / "data" / "demo" / "real_seed.json"
MODELS = ROOT / "data" / "ml" / "models"
CASES_MANIFEST = ROOT / "eval" / "cases" / "disputes" / "manifest.json"


def build(source: Path = SOURCE, seed_path: Path = SEED, models: Path = MODELS, bundle: Path = BUNDLE,
          lock_path: Path = LOCK, log=print) -> dict[str, Any]:
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    customers = {i["customer_id"]: i["scenario"] for i in seed["identities"]}
    if len(customers) != len(seed["identities"]):
        raise SystemExit("the seed uses one customer for two scenarios")
    staging = bundle.with_name(bundle.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "models").mkdir(parents=True)
    try:
        for name in ("learned.pkl", "systems.pkl"):
            shutil.copyfile(models / name, staging / "models" / name)
        counts = slice_warehouse(source, staging / lock.WAREHOUSE, customers, source.name)
        shutil.copyfile(seed_path, staging / lock.SEED)
        disposition = load_learned(staging / "models")  # the copies, not the originals
        runs = validate_seed(staging / lock.WAREHOUSE, seed, disposition)
        failed = {name: [r.summary() for r in rs if not r.ok] for name, rs in runs.items() if not all(r.ok for r in rs)}
        if failed or set(runs) != {i["scenario"] for i in seed["identities"]}:
            raise SystemExit(f"scenarios failed on the slim warehouse: {json.dumps(failed, default=str)[:2000]}")
        data = {
            "format": lock.FORMAT,
            "note": "Demo bundle for deploy/Dockerfile. The bundle is git-ignored (organizer-derived rows and the "
                    "fitted model); this lock is committed. Rebuild with `make demo-artifacts`, then commit this file.",
            "files": lock.describe(staging),
            "as_of": seed["as_of"],
            "disposition_model": disposition.name,
            "scenarios": [i["scenario"] for i in seed["identities"]],
            "languages_validated": ["es", "pt"],
            "warehouse_content_sha256": content_digest(staging / lock.WAREHOUSE),
            "warehouse_rows": counts,
            "source_warehouse": source.name,
            "model_data_version": json.loads(CASES_MANIFEST.read_text(encoding="utf-8"))["data_version"]
            if CASES_MANIFEST.is_file() else None,
        }
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(bundle, ignore_errors=True)
    staging.rename(bundle)
    lock_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    lock.verify(bundle, lock_path)
    size = sum(f["bytes"] for f in data["files"].values())
    log(f"bundle {bundle} ({size / 1e6:.1f} MB, {len(data['files'])} files), {len(runs)} scenarios validated in es and "
        f"pt with {disposition.name}; lock {lock_path}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--seed", type=Path, default=SEED)
    parser.add_argument("--models", type=Path, default=MODELS)
    args = parser.parse_args(argv)
    for path, how in ((args.source, "make pipeline gold SOURCE=data/raw TARGET=data/warehouse_real.duckdb"),
                      (args.seed, "uv run python -m deploy.demo_select"),
                      (args.models / "systems.pkl", "uv run python -m ml.train")):
        if not path.exists():
            raise SystemExit(f"{path} not found: {how}")
    build(args.source, args.seed, args.models)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

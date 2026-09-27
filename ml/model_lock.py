"""The committed disposition model in ml/models/ and its sha256 lock.

    uv run python -m ml.model_lock            # verify ml/models/*.pkl against ml/models/models.lock.json (make model-verify)
    uv run python -m ml.model_lock --write    # copy data/ml/models (output of ml.train) to ml/models and rewrite the lock

The two files are the fitted learned ranker (``learned.pkl``) and the deciders (``systems.pkl``) that ``ml/train.py``
writes to the git-ignored ``data/ml/models``. They hold model parameters only: coefficients, tree nodes with their
training counts, scaler means and feature-space bin thresholds (ratios, day distances, similarity scores). They hold
no customer or transaction id, no text and no raw amount. The service loads them when ``data/ml/models`` is absent,
after checking both hashes here (agent/orchestrator/disposition.py).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMITTED_DIR = ROOT / "ml" / "models"
LOCK_NAME = "models.lock.json"
FILES = ("learned.pkl", "systems.pkl")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(models_dir: Path = COMMITTED_DIR) -> list[str]:
    """Problems found; empty when every locked file exists with its locked size and sha256."""
    lock_path = models_dir / LOCK_NAME
    if not lock_path.is_file():
        return [f"{lock_path} not found"]
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    problems = [f"{name} is not locked" for name in FILES if name not in lock["files"]]
    for name, want in sorted(lock["files"].items()):
        path = models_dir / name
        if not path.is_file():
            problems.append(f"{name} missing")
        elif path.stat().st_size != want["bytes"] or sha256(path) != want["sha256"]:
            problems.append(f"{name} does not match its locked sha256")
    return problems


def write(source: Path, models_dir: Path = COMMITTED_DIR) -> dict:
    """Copy the trained files from ``source`` and lock them with the data version of ml/reports/fitted.json."""
    fitted = json.loads((ROOT / "ml" / "reports" / "fitted.json").read_text(encoding="utf-8"))
    models_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in FILES:
        shutil.copyfile(source / name, models_dir / name)
        files[name] = {"bytes": (models_dir / name).stat().st_size, "sha256": sha256(models_dir / name)}
    lock = {"format": 1, "data_version": fitted["data_version"],
            "disposition_model": f"learned_ranker_disposition:{fitted['systems']['learned_ranker_disposition']['decider']}",
            "written_by": "ml/model_lock.py --write, from the output of ml/train.py", "files": files}
    (models_dir / LOCK_NAME).write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8", newline="\n")
    return lock


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="copy data/ml/models into ml/models and rewrite the lock")
    args = ap.parse_args()
    if args.write:
        lock = write(ROOT / "data" / "ml" / "models")
        print(f"locked {', '.join(lock['files'])} ({lock['disposition_model']}, data version {lock['data_version']})")
        return
    problems = verify()
    if problems:
        raise SystemExit("model lock check failed: " + "; ".join(problems))
    print(f"ml/models verified against {LOCK_NAME}: {', '.join(FILES)}")


if __name__ == "__main__":
    main()

"""Check a demo bundle against its committed lock. Standard library only: it runs in the image build and at startup.

    python -m deploy.lock verify deploy/demo-bundle deploy/demo-bundle.lock.json

The lock (deploy/demo-bundle.lock.json, committed) names every file of the bundle with its size and sha256. A bundle
passes only when it holds exactly those files with exactly those bytes, and its seed carries the lock's demo clock.
The bundle itself (organizer-derived rows, the learned model) is git-ignored and never committed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

FORMAT = 1
MODELS = ("models/learned.pkl", "models/systems.pkl")
WAREHOUSE = "warehouse.duckdb"
SEED = "demo_customers.json"
FILES = (*MODELS, WAREHOUSE, SEED)


class BundleError(RuntimeError):
    """The bundle does not match its lock."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def describe(bundle: Path) -> dict[str, dict[str, Any]]:
    """Size and sha256 of every file in the bundle, keyed by posix path relative to it."""
    return {p.relative_to(bundle).as_posix(): {"bytes": p.stat().st_size, "sha256": sha256(p)}
            for p in sorted(bundle.rglob("*")) if p.is_file()}


def read_lock(lock: Path) -> dict[str, Any]:
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleError(f"cannot read the lock {lock}: {type(exc).__name__}") from exc
    if data.get("format") != FORMAT or set(data.get("files", {})) != set(FILES):
        raise BundleError(f"{lock} is not a format-{FORMAT} demo bundle lock with files {', '.join(FILES)}")
    return data


def verify(bundle: Path, lock: Path) -> dict[str, Any]:
    """Raise BundleError unless `bundle` matches `lock` exactly. Returns the lock."""
    data = read_lock(lock)
    if not bundle.is_dir():
        raise BundleError(f"bundle directory not found: {bundle}")
    found = describe(bundle)
    extra, missing = sorted(set(found) - set(data["files"])), sorted(set(data["files"]) - set(found))
    if extra or missing:
        raise BundleError(f"bundle files differ from the lock: missing {missing}, unexpected {extra}")
    wrong = sorted(name for name, want in data["files"].items() if found[name] != want)
    if wrong:
        raise BundleError(f"bundle files do not match their locked size or sha256: {', '.join(wrong)}")
    seed = json.loads((bundle / SEED).read_text(encoding="utf-8"))
    if seed.get("as_of") != data.get("as_of"):
        raise BundleError("the seed's demo clock differs from the lock's")
    return data


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3 or args[0] != "verify":
        print("usage: python -m deploy.lock verify <bundle-dir> <lock-file>", file=sys.stderr)
        return 2
    try:
        data = verify(Path(args[1]), Path(args[2]))
    except BundleError as exc:
        print(f"demo bundle REJECTED: {exc}", file=sys.stderr)
        return 1
    print(f"demo bundle verified: {len(data['files'])} files, disposition model {data['disposition_model']}, "
          f"demo clock {data['as_of']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build the two files a server deploy needs, from the committed HEAD and the verified demo bundle.

    uv run python -m deploy.release              # make release

Writes to release/ (git-ignored):
  cautela-<sha>.tar.gz              `git archive HEAD` of pyproject.toml, uv.lock, agent, api, ml, data_engineering,
                                    deploy (the committed lock included), under the prefix cautela-<sha>/
  cautela-<sha>-demo-bundle.tar.gz  deploy/demo-bundle/ under cautela-<sha>/deploy/demo-bundle/, so both extract into
                                    the same release directory
  SHA256SUMS                        sha256 of both, for `sha256sum -c` on the server

It refuses when the working tree's lock differs from HEAD's (the tarball would carry a lock that does not describe
the bundle), when the bundle does not match the lock, or when the code tarball holds anything that looks like data
or a secret. The bundle tarball is deterministic: fixed order, mtime 0, root owner, mode 0644 for files.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

from deploy import lock

ROOT = Path(__file__).resolve().parents[1]
PATHS = ("pyproject.toml", "uv.lock", "agent", "api", "ml", "data_engineering", "deploy",
         "docs/schemas/handoff.schema.json")  # read at runtime by agent/handoff.py
LOCK_REL = "deploy/demo-bundle.lock.json"
FORBIDDEN = (".env", ".duckdb", ".pkl", ".parquet", ".csv")


def git(*args: str) -> str:
    return subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()


def check_lock_committed() -> None:
    if LOCK_REL not in git("ls-files", "--", LOCK_REL).splitlines():
        raise SystemExit(f"{LOCK_REL} is not committed: commit it after `make demo-artifacts`")
    if git("status", "--porcelain", "--", LOCK_REL):
        raise SystemExit(f"{LOCK_REL} differs from HEAD: commit the lock that describes the bundle")


def code_tarball(sha: str, out: Path) -> Path:
    path = out / f"cautela-{sha}.tar.gz"
    git("archive", "--format=tar.gz", f"--prefix=cautela-{sha}/", "-o", str(path), "HEAD", *PATHS)
    with tarfile.open(path, "r:gz") as tar:
        names = tar.getnames()
    bad = [n for n in names if n.endswith(FORBIDDEN) or "/data/" in n or "/demo-bundle/" in n]
    if bad:
        path.unlink()
        raise SystemExit(f"the code tarball holds data or secrets: {bad[:5]}")
    if f"cautela-{sha}/{LOCK_REL}" not in names:
        path.unlink()
        raise SystemExit("the code tarball has no demo bundle lock")
    return path


def bundle_tarball(sha: str, bundle: Path, out: Path) -> Path:
    path = out / f"cautela-{sha}-demo-bundle.tar.gz"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        prefix = f"cautela-{sha}/deploy/demo-bundle"
        for name in sorted(lock.describe(bundle)):
            info = tarfile.TarInfo(f"{prefix}/{name}")
            data = (bundle / name).read_bytes()
            info.size, info.mtime, info.mode, info.uid, info.gid = len(data), 0, 0o644, 0, 0
            tar.addfile(info, io.BytesIO(data))
    with path.open("wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, filename="") as gz:
        gz.write(raw.getvalue())
    return path


def release(out: Path, bundle: Path = ROOT / "deploy" / "demo-bundle") -> list[Path]:
    check_lock_committed()
    locked = lock.verify(bundle, ROOT / LOCK_REL)
    sha = git("rev-parse", "--short", "HEAD")
    out.mkdir(parents=True, exist_ok=True)
    files = [code_tarball(sha, out), bundle_tarball(sha, bundle, out)]
    sums = "".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n" for f in files)
    (out / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
    for f in files:
        print(f"{f.name}  {f.stat().st_size / 1e6:.2f} MB")
    print(f"demo bundle: {locked['disposition_model']}, clock {locked['as_of']}, scenarios "
          f"{', '.join(locked['scenarios'])}")
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "release")
    args = parser.parse_args(argv)
    try:
        release(args.out)
    except lock.BundleError as exc:
        raise SystemExit(f"demo bundle REJECTED: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

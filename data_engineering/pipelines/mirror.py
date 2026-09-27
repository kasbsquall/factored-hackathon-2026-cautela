"""Copy the organizer delivery into a local directory, so the pipeline reads the same files a judge can inspect.

    uv run python -m data_engineering.pipelines.mirror --dest data/raw          # make mirror

The source is LATAM_BANK_S3_URI (from the environment or the git-ignored .env), which must name the prefix that holds
the table folders (s3://<bucket>/data). --source takes any s3:// URI or local directory instead. Each file keeps its
bytes and gets the source's modification time (S3 LastModified), so the file ledger fingerprints what the delivery
holds. A second run copies only files whose size or time changed, and files of the selected tables that the source
no longer has are removed from the destination (data_engineering/freshness/stage.py does the copy).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import duckdb

from data_engineering.contracts.loader import load_contracts
from data_engineering.freshness import stage
from data_engineering.pipelines.env import load_env_file
from data_engineering.pipelines.source import SOURCE_ENV_VAR, connect_source, parse_source, resolve_source


def mirror(source: str, dest: str | Path, tables: list[str], workers: int = stage.DEFAULT_WORKERS) -> dict:
    """Make `dest` an exact copy of the selected tables of `source`. Fails when a table has no file there."""
    con = duckdb.connect()
    try:
        connect_source(con, parse_source(source))
        started = time.perf_counter()
        result = stage.copy_state(con, source, tables, dest, workers)
        result["seconds"] = round(time.perf_counter() - started, 1)
        landed = stage.list_state(con, parse_source(str(dest)), tables)
    finally:
        con.close()
    empty = [t for t in tables
             if not any(f.rel_path.startswith((f"{t}/", f"{t}.")) for f in landed)]
    if empty:
        raise ValueError(f"no files for {', '.join(empty)} under the source: {SOURCE_ENV_VAR} must end in the "
                         "prefix that holds the table folders (s3://<bucket>/data)")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", help=f"s3:// URI or local directory (default: ${SOURCE_ENV_VAR})")
    parser.add_argument("--dest", default="data/raw", help="local directory to fill (git-ignored)")
    parser.add_argument("--tables", nargs="*", help="subset of tables (default: all 13 contract tables)")
    parser.add_argument("--workers", type=int, default=stage.DEFAULT_WORKERS)
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)
    load_env_file(args.env_file)
    tables = [t for v in (args.tables or []) for t in v.split(",") if t] or list(load_contracts())
    try:
        source = resolve_source(args.source, os.environ)
        result = mirror(source, args.dest, tables, args.workers)
    except (ValueError, duckdb.Error) as exc:
        print(f"mirror failed: {exc}", file=sys.stderr)
        return 1
    print(f"{args.dest}: {result['files']} files, {result['bytes'] / 1e9:.2f} GB; copied {result['copied']} "
          f"({result['copied_bytes'] / 1e9:.2f} GB), kept {result['kept']}, removed {result['removed']}, "
          f"{result['seconds']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

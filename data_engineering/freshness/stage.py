"""Copy one delivery state (a local directory or an s3:// prefix) into a local directory, file by file.

The copy keeps each file's bytes and sets its modification time to the source's (S3 LastModified for s3://), so
the pipeline's file ledger sees the same size and time it would see reading the source directly. Only files whose
size or time differ are copied, and files of the selected tables that the source no longer has are removed, so the
destination ends up as an exact copy of that state for those tables. Reads go through DuckDB (`read_blob`), which
already holds the S3 credentials from the environment; nothing is logged about them.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb

from data_engineering.pipelines.source import SourceFile, SourceLocation, list_files, parse_source

DEFAULT_WORKERS = 16


def list_state(con: duckdb.DuckDBPyConnection, loc: SourceLocation, tables: Iterable[str]) -> list[SourceFile]:
    """Every file of the selected tables, with size and modification time, without reading file bodies."""
    return sorted((f for table in tables for f in list_files(con, loc, table)), key=lambda f: f.rel_path)


def _fetch(con: duckdb.DuckDBPyConnection, f: SourceFile, dest: Path) -> int:
    cursor = con.cursor()  # one cursor per thread; DuckDB runs the reads in parallel
    try:
        content = cursor.execute("SELECT content FROM read_blob(?)", [f.uri]).fetchone()[0]
    finally:
        cursor.close()
    target = dest / f.rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(content)
    if f.modified_ms is not None:
        ns = f.modified_ms * 1_000_000
        os.utime(partial, ns=(ns, ns))
    os.replace(partial, target)
    return len(content)


def _prune_empty_dirs(root: Path) -> None:
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(path.iterdir()):
            path.rmdir()


def copy_state(con: duckdb.DuckDBPyConnection, source: str, tables: Iterable[str], dest: str | Path,
               workers: int = DEFAULT_WORKERS) -> dict:
    """Make `dest` hold exactly the selected tables' files of `source`. Returns what was copied, kept and removed.

    `con` must already be connected to the source (see pipelines.source.connect_source for s3://).
    """
    tables = list(tables)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    wanted = list_state(con, parse_source(source), tables)
    have = {f.rel_path: f.fingerprint for f in list_state(con, parse_source(str(dest)), tables)}
    todo = [f for f in wanted if have.get(f.rel_path) != f.fingerprint]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        copied_bytes = sum(pool.map(lambda f: _fetch(con, f, dest), todo))
    keep = {f.rel_path for f in wanted}
    removed = [rel for rel in have if rel not in keep]
    for rel in removed:
        (dest / rel).unlink()
    _prune_empty_dirs(dest)
    return {"files": len(wanted), "bytes": sum(f.size or 0 for f in wanted), "copied": len(todo),
            "copied_bytes": copied_bytes, "kept": len(wanted) - len(todo), "removed": len(removed)}

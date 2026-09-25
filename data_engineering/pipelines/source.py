"""Source-agnostic access to the raw files: a local directory or an s3:// prefix.

Both go through DuckDB (`glob`, `parquet_schema`, `read_parquet`, `read_csv`), so the rest of the pipeline never
branches on where the data lives. S3 credentials are read from environment variables only (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
AWS_REGION, default us-east-2), normally loaded from the git-ignored .env file, and are never written to logs,
reports or the warehouse. LATAM_BANK_S3_URI gives the default source when --source is omitted.

Expected layout under the source root, per table (the real delivery layout is still unconfirmed, so both forms
are accepted):
  <root>/<table>/**/*.parquet|csv     partitioned folders, e.g. year=2026/month=04/day=01/part-0.parquet
  <root>/<table>.parquet|csv          a single file per table
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import duckdb

FORMATS = ("parquet", "csv")
DEFAULT_S3_REGION = "us-east-2"  # region of the organizer bucket
SOURCE_ENV_VAR = "LATAM_BANK_S3_URI"
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")


@dataclass(frozen=True)
class SourceLocation:
    kind: Literal["local", "s3"]
    root: str  # forward slashes, no trailing slash
    bucket: str | None = None
    prefix: str | None = None

    def patterns(self, table: str) -> list[tuple[str, str]]:
        nested = [(f"{self.root}/{table}/**/*.{fmt}", fmt) for fmt in FORMATS]
        single = [(f"{self.root}/{table}.{fmt}", fmt) for fmt in FORMATS]
        return nested + single

    def relative(self, uri: str) -> str:
        norm = uri.replace("\\", "/")
        return norm[len(self.root) + 1:] if norm.startswith(self.root + "/") else norm


@dataclass(frozen=True)
class SourceFile:
    uri: str  # what DuckDB reads
    rel_path: str  # path relative to the source root; used for lineage and the file ledger
    fmt: str
    encoding: str = "utf-8"  # set by detect_encodings for CSV; Parquet strings are UTF-8 by specification
    has_bom: bool = False


_PARTITION_SEGMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=([^/]*)$")


def path_partitions(rel_path: str) -> dict[str, str]:
    """Hive-style key=value folder segments of a file path, e.g. {'year': '2023', 'month': '06', 'day': '17'}."""
    out = {}
    for segment in rel_path.split("/")[:-1]:
        match = _PARTITION_SEGMENT.match(segment)
        if match:
            out[match.group(1).lower()] = match.group(2)
    return out


def detect_encodings(con: duckdb.DuckDBPyConnection, files: list[SourceFile]) -> list[SourceFile]:
    """Check the bytes of every CSV file: strict UTF-8 (with or without BOM) or not.

    A file that is not valid UTF-8 is read as Latin-1, the usual alternative for Spanish text exported on Windows,
    and flagged in the report. Latin-1 decoding never fails, so a wrong guess would show up as odd characters;
    the replacement-character check after loading is the backstop.
    """
    csv = [f for f in files if f.fmt == "csv"]
    if not csv:
        return files
    rows = con.execute(
        r"SELECT replace(filename, '\', '/'), text IS NULL, coalesce(starts_with(text, chr(65279)), false) "
        f"FROM (SELECT filename, try(decode(content)) AS text FROM read_blob({sql_list([f.uri for f in csv])}))"
    ).fetchall()
    found = {uri: (invalid, bom) for uri, invalid, bom in rows}
    out = []
    for f in files:
        if f.fmt == "csv":
            invalid, bom = found[f.uri]
            f = replace(f, encoding="latin-1" if invalid else "utf-8", has_bom=bool(bom))
        out.append(f)
    return out


def parse_source(uri: str) -> SourceLocation:
    """Parse a local path or an s3://bucket/prefix URI. Credentials embedded in the URI are rejected."""
    if not uri or not uri.strip():
        raise ValueError("source must not be empty")
    uri = uri.strip()
    if "://" not in uri:
        return SourceLocation("local", Path(uri).resolve().as_posix().rstrip("/"))
    scheme, rest = uri.split("://", 1)
    if scheme.lower() != "s3":
        raise ValueError(f"unsupported scheme {scheme!r}: use a local path or s3://bucket/prefix")
    if any(ch in rest for ch in "?#"):
        raise ValueError("query strings and fragments are not allowed in the source URI")
    bucket, _, prefix = rest.partition("/")
    if "@" in bucket or ":" in bucket:
        raise ValueError("credentials must come from environment variables, never from the URI")
    if not _BUCKET.match(bucket):
        raise ValueError(f"invalid S3 bucket name {bucket!r}")
    prefix = prefix.strip("/")
    root = f"s3://{bucket}" + (f"/{prefix}" if prefix else "")
    return SourceLocation("s3", root, bucket, prefix)


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def s3_secret_sql(env: Mapping[str, str]) -> str:
    """Build the DuckDB CREATE SECRET statement from AWS_* environment variables.

    The returned SQL contains the secret itself: execute it, never log it. Without explicit keys the AWS
    credential chain is used (profile, SSO, instance role).
    """
    key, secret = env.get("AWS_ACCESS_KEY_ID"), env.get("AWS_SECRET_ACCESS_KEY")
    if bool(key) != bool(secret):
        raise ValueError("set both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, or neither")
    parts = ["TYPE s3"]
    if key:
        parts += ["PROVIDER config", f"KEY_ID {_q(key)}", f"SECRET {_q(secret)}"]
        if env.get("AWS_SESSION_TOKEN"):
            parts.append(f"SESSION_TOKEN {_q(env['AWS_SESSION_TOKEN'])}")
    else:
        parts.append("PROVIDER credential_chain")
    parts.append(f"REGION {_q(s3_region(env))}")
    endpoint = env.get("AWS_ENDPOINT_URL")
    if endpoint:
        use_ssl = not endpoint.lower().startswith("http://")
        host = re.sub(r"^https?://", "", endpoint, flags=re.IGNORECASE).rstrip("/")
        parts += [f"ENDPOINT {_q(host)}", "URL_STYLE 'path'", f"USE_SSL {str(use_ssl).lower()}"]
    return f"CREATE OR REPLACE SECRET cautela_s3 ({', '.join(parts)})"


def s3_region(env: Mapping[str, str]) -> str:
    return env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or DEFAULT_S3_REGION


def describe_s3_auth(env: Mapping[str, str]) -> str:
    """A log-safe summary of how S3 will authenticate (no secret material)."""
    key = env.get("AWS_ACCESS_KEY_ID")
    how = f"static key ending ...{key[-4:]}" if key else "AWS credential chain"
    return f"{how}, region {s3_region(env)}"


def resolve_source(cli_value: str | None, env: Mapping[str, str]) -> str:
    """--source wins; otherwise LATAM_BANK_S3_URI (normally set in the git-ignored .env)."""
    value = cli_value or env.get(SOURCE_ENV_VAR)
    if not value:
        raise ValueError(f"no source: pass --source or set {SOURCE_ENV_VAR} in .env")
    return value


def connect_source(con: duckdb.DuckDBPyConnection, loc: SourceLocation, env: Mapping[str, str] | None = None) -> None:
    if loc.kind != "s3":
        return
    env = os.environ if env is None else env
    con.execute("INSTALL httpfs; LOAD httpfs;")
    if not env.get("AWS_ACCESS_KEY_ID"):
        con.execute("INSTALL aws; LOAD aws;")
    con.execute(s3_secret_sql(env))


def sql_list(values: list[str]) -> str:
    return "[" + ", ".join(_q(v) for v in values) + "]"


def list_files(con: duckdb.DuckDBPyConnection, loc: SourceLocation, table: str) -> list[SourceFile]:
    found: dict[str, SourceFile] = {}
    for pattern, fmt in loc.patterns(table):
        for (uri,) in con.execute("SELECT file FROM glob(?)", [pattern]).fetchall():
            norm = uri.replace("\\", "/")
            found.setdefault(norm, SourceFile(norm, loc.relative(norm), fmt))
    return sorted(found.values(), key=lambda f: f.rel_path)


def file_schemas(con: duckdb.DuckDBPyConnection, files: list[SourceFile]) -> dict[str, dict[str, str]]:
    """Column name (lower case) -> type signature, per file. Used for drift detection before reading."""
    by_uri = {f.uri: f for f in files}
    schemas: dict[str, dict[str, str]] = {f.rel_path: {} for f in files}
    parquet = [f.uri for f in files if f.fmt == "parquet"]
    if parquet:
        rows = con.execute(
            f"SELECT file_name, name, type, converted_type FROM parquet_schema({sql_list(parquet)}) "
            "WHERE num_children IS NULL OR num_children = 0"
        ).fetchall()
        for file_name, name, physical, converted in rows:
            rel = by_uri.get(file_name.replace("\\", "/"))
            if rel is not None:
                schemas[rel.rel_path][name.lower()] = converted or physical
    for f in (f for f in files if f.fmt == "csv"):
        described = con.execute(f"DESCRIBE SELECT * FROM {read_relation_sql([f])}")
        schemas[f.rel_path] = {row[0].lower(): "VARCHAR" for row in described.fetchall() if row[0] != "filename"}
    return schemas


def read_relation_sql(files: list[SourceFile]) -> str:
    """A FROM-clause expression reading files of one format and encoding, aligning columns by name.

    Hive partitioning is switched off: year/month/day folders are metadata about the file, handled explicitly
    (see path_partitions), and must not appear as data columns that look like schema drift.
    """
    kinds = {(f.fmt, f.encoding) for f in files}
    if len(kinds) != 1:
        raise ValueError("read_relation_sql expects files of a single format and encoding")
    fmt, encoding = kinds.pop()
    uris = sql_list([f.uri for f in files])
    if fmt == "parquet":
        return f"read_parquet({uris}, union_by_name = true, filename = true, hive_partitioning = false)"
    return (f"read_csv({uris}, union_by_name = true, filename = true, all_varchar = true, header = true, "
            f"hive_partitioning = false, encoding = {_q(encoding)})")

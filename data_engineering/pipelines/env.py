"""Minimal .env loader, so the S3 credentials can live only in the git-ignored .env file.

Supports KEY=VALUE lines, blank lines, # comments, an optional `export ` prefix and single or double quotes.
Variables already set in the environment win over the file. Values are never printed or logged; callers only
get back the names that were loaded.
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_env_file(path: str | Path = ".env", environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Load variables from `path` into `environ` without overriding existing ones. Returns the names loaded."""
    environ = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for key, value in parse_env(path.read_text(encoding="utf-8")).items():
        if value and not environ.get(key):
            environ[key] = value
            loaded.append(key)
    return sorted(loaded)

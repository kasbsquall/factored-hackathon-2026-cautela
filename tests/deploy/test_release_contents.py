"""The release tarball and the image must carry every repository file the service reads at runtime.

Regression: the first public releases shipped without docs/schemas/handoff.schema.json, so every handoff failed
validation in production and the customer was told there was a technical problem.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path

from deploy.release import PATHS

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "docs/schemas/handoff.schema.json"


def test_handoff_schema_is_in_the_release_the_build_context_and_the_image():
    assert SCHEMA in PATHS
    assert f"!{SCHEMA}" in (ROOT / "deploy" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert f"COPY {SCHEMA} ./{SCHEMA}" in dockerfile and "COPY --from=build /src/docs /app/docs" in dockerfile


def test_a_handoff_validates_from_an_extracted_release(tmp_path):
    archive = tmp_path / "release.tar"
    subprocess.run(["git", "archive", "--format=tar", "-o", str(archive), "HEAD", *PATHS], cwd=ROOT, check=True)
    with tarfile.open(archive) as tar:
        tar.extractall(tmp_path / "rel", filter="data")
    probe = "from agent.handoff import _validator; _validator(); print('ok')"
    out = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path / "rel", capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr[-500:]

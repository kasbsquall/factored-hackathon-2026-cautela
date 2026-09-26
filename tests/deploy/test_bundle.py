"""The demo bundle: lock verification, the bundle builder, the release tarball and the service's refusal to start
without a verified bundle and the learned model. Runs on the synthetic fixture; tests that need the git-ignored
learned artifacts or the organizer-derived bundle skip when those are absent. No network."""

from __future__ import annotations

import io
import json
import pickle
import shutil
import tarfile
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from api.settings import ROOT, ApiSettings
from data_engineering.slice import slice_warehouse
from deploy import bundle as bundle_mod
from deploy import lock, release, serve
from deploy.demo_select import Run
from tests.agent.conftest import warehouse  # noqa: F401 (fixture)

MODELS = ROOT / "data" / "ml" / "models"
HAS_MODEL = (MODELS / "learned.pkl").is_file() and (MODELS / "systems.pkl").is_file()
FIXTURE_SEED = ROOT / "api" / "seed" / "demo_customers.json"


def write_lock(bundle: Path, lock_path: Path, model: str = "learned_ranker_disposition:test") -> dict:
    seed = json.loads((bundle / lock.SEED).read_text(encoding="utf-8"))
    data = {"format": lock.FORMAT, "files": lock.describe(bundle), "as_of": seed["as_of"], "disposition_model": model,
            "scenarios": [i["scenario"] for i in seed["identities"]]}
    lock_path.write_text(json.dumps(data), encoding="utf-8")
    return data


@pytest.fixture()
def fake_bundle(tmp_path) -> tuple[Path, Path]:
    """A bundle with the right file set and placeholder bytes: enough for lock checks, never unpickled."""
    b = tmp_path / "bundle"
    (b / "models").mkdir(parents=True)
    (b / "models" / "learned.pkl").write_bytes(b"not a pickle 1")
    (b / "models" / "systems.pkl").write_bytes(b"not a pickle 2")
    (b / lock.WAREHOUSE).write_bytes(b"not a warehouse")
    (b / lock.SEED).write_text(json.dumps({"as_of": "2026-06-19T12:00:00+00:00", "identities": []}), encoding="utf-8")
    lock_path = tmp_path / "demo-bundle.lock.json"
    write_lock(b, lock_path)
    return b, lock_path


# ---- lock --------------------------------------------------------------------------------------------------------
def test_a_matching_bundle_verifies(fake_bundle):
    b, lock_path = fake_bundle
    assert lock.verify(b, lock_path)["as_of"] == "2026-06-19T12:00:00+00:00"
    assert lock.main(["verify", str(b), str(lock_path)]) == 0


@pytest.mark.parametrize("tamper", ["byte", "extra", "missing", "clock"])
def test_any_difference_from_the_lock_is_rejected(fake_bundle, tamper):
    b, lock_path = fake_bundle
    if tamper == "byte":
        (b / "models" / "learned.pkl").write_bytes(b"not a pickle 9")
    elif tamper == "extra":
        (b / "models" / "decision.pkl").write_bytes(b"stale")
    elif tamper == "missing":
        (b / lock.WAREHOUSE).unlink()
    else:
        (b / lock.SEED).write_text(json.dumps({"as_of": "2026-06-20T12:00:00+00:00", "identities": []}),
                                   encoding="utf-8")
    with pytest.raises(lock.BundleError):
        lock.verify(b, lock_path)
    assert lock.main(["verify", str(b), str(lock_path)]) == 1


def test_a_lock_of_another_shape_is_rejected(fake_bundle, tmp_path):
    b, _ = fake_bundle
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"format": 1, "files": {"warehouse.duckdb": {}}}), encoding="utf-8")
    with pytest.raises(lock.BundleError, match="format-1"):
        lock.verify(b, other)
    with pytest.raises(lock.BundleError, match="cannot read"):
        lock.verify(b, tmp_path / "absent.json")


# ---- the service -----------------------------------------------------------------------------------------------
def test_without_a_bundle_the_fixture_path_is_unchanged():
    settings = ApiSettings()
    assert serve.load_bundle(settings, {}) == (settings, None, None)


def test_a_bundle_that_fails_its_lock_is_never_unpickled(fake_bundle, monkeypatch):
    b, lock_path = fake_bundle
    (b / lock.WAREHOUSE).write_bytes(b"changed")
    loaded = []
    monkeypatch.setattr(serve.LearnedDisposition, "load", classmethod(lambda cls, d: loaded.append(d)))
    with pytest.raises(lock.BundleError):
        serve.load_bundle(ApiSettings(), {"CAUTELA_BUNDLE_DIR": str(b), "CAUTELA_BUNDLE_LOCK": str(lock_path)})
    assert loaded == []


def test_a_verified_bundle_whose_model_does_not_load_stops_the_service(fake_bundle):
    b, lock_path = fake_bundle
    with pytest.raises(lock.BundleError, match="learned disposition model did not load"):
        serve.load_bundle(ApiSettings(), {"CAUTELA_BUNDLE_DIR": str(b), "CAUTELA_BUNDLE_LOCK": str(lock_path)})


def test_a_rule_model_under_a_learned_name_is_refused(tmp_path, monkeypatch, fake_bundle):
    b, lock_path = fake_bundle

    class Rules:
        name = "rules_fixed_baseline"

    monkeypatch.setattr(serve.LearnedDisposition, "load", classmethod(lambda cls, d: Rules()))
    with pytest.raises(lock.BundleError, match="rules_fixed_baseline"):
        serve.load_bundle(ApiSettings(), {"CAUTELA_BUNDLE_DIR": str(b), "CAUTELA_BUNDLE_LOCK": str(lock_path)})


def test_main_exits_instead_of_serving_without_a_verified_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("CAUTELA_BUNDLE_DIR", str(tmp_path / "no-bundle"))
    monkeypatch.setenv("CAUTELA_BUNDLE_LOCK", str(tmp_path / "no-lock.json"))
    monkeypatch.setenv("CAUTELA_AUDIT_DIR", str(tmp_path / "audit"))
    started = []
    monkeypatch.setattr(serve.uvicorn.Server, "run", lambda self: started.append(True))
    with pytest.raises(SystemExit) as exc:
        serve.main()
    assert exc.value.code == 1 and started == []


@pytest.mark.skipif(not HAS_MODEL, reason="learned artifacts are git-ignored; run `uv run python -m ml.train`")
def test_a_fixture_bundle_serves_with_the_learned_model_and_its_clock(warehouse, tmp_path):
    seed = json.loads(FIXTURE_SEED.read_text(encoding="utf-8"))
    b = tmp_path / "bundle"
    (b / "models").mkdir(parents=True)
    for name in ("learned.pkl", "systems.pkl"):
        shutil.copyfile(MODELS / name, b / "models" / name)
    with duckdb.connect(str(warehouse), read_only=True) as con:
        ids = {con.execute("SELECT customer_id FROM silver.customers WHERE document_number = ?",
                           [i["document_number"]]).fetchone()[0]: i["scenario"] for i in seed["identities"]}
    slice_warehouse(warehouse, b / lock.WAREHOUSE, ids, "fixture")
    shutil.copyfile(FIXTURE_SEED, b / lock.SEED)
    name = serve.LearnedDisposition.load(b / "models").name
    lock_path = tmp_path / "lock.json"
    write_lock(b, lock_path, model=name)
    env = {"CAUTELA_BUNDLE_DIR": str(b), "CAUTELA_BUNDLE_LOCK": str(lock_path)}
    app = serve.build_app(ApiSettings(audit_dir=None, console_key="test-console-key-0123456789"), environ=env)
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["disposition_model"] == name and name.startswith("learned_ranker_disposition")
        assert health["demo_bundle"]["verified"] is True and health["demo_bundle"]["as_of"] == seed["as_of"]
        assert health["service_clock"][:10] == seed["as_of"][:10]
        assert {i["scenario"] for i in client.get("/demo/identities").json()} == set(ids.values())


# ---- the builder ---------------------------------------------------------------------------------------------------
@pytest.fixture()
def build_inputs(warehouse, tmp_path, monkeypatch):
    with duckdb.connect(str(warehouse), read_only=True) as con:
        cid, doc = con.execute("SELECT customer_id, document_number FROM silver.customers ORDER BY 1 LIMIT 1").fetchone()
    seed = {"as_of": "2026-06-01T12:00:00+00:00",
            "identities": [{"scenario": "normal", "customer_id": cid, "document_number": doc}]}
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    models = tmp_path / "models"
    models.mkdir()
    for name in ("learned.pkl", "systems.pkl"):
        (models / name).write_bytes(pickle.dumps(name))

    class Model:
        name = "learned_ranker_disposition:stub"

    monkeypatch.setattr(bundle_mod, "load_learned", lambda d: Model())
    return seed_path, models, tmp_path / "out" / "demo-bundle", tmp_path / "out" / "lock.json"


def test_the_builder_locks_only_a_bundle_whose_scenarios_pass(warehouse, build_inputs, monkeypatch):
    seed_path, models, out, lock_path = build_inputs
    monkeypatch.setattr(bundle_mod, "validate_seed", lambda w, s, d: {"normal": [Run("es", ok=True),
                                                                                  Run("pt", ok=True)]})
    data = bundle_mod.build(warehouse, seed_path, models, out, lock_path, log=lambda _: None)
    assert lock.verify(out, lock_path) == json.loads(lock_path.read_text(encoding="utf-8"))
    assert data["disposition_model"] == "learned_ranker_disposition:stub" and data["scenarios"] == ["normal"]
    assert data["warehouse_content_sha256"] and data["files"]["models/learned.pkl"]["bytes"] > 0
    text = lock_path.read_text(encoding="utf-8")
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    ident = seed["identities"][0]
    assert ident["customer_id"] not in text and ident["document_number"] not in text, "the committed lock names no one"


def test_the_builder_removes_everything_when_a_scenario_fails(warehouse, build_inputs, monkeypatch):
    seed_path, models, out, lock_path = build_inputs
    monkeypatch.setattr(bundle_mod, "validate_seed", lambda w, s, d: {"normal": [Run("es", ok=True),
                                                                                  Run("pt", problem="x")]})
    with pytest.raises(SystemExit, match="scenarios failed"):
        bundle_mod.build(warehouse, seed_path, models, out, lock_path, log=lambda _: None)
    assert not out.exists() and not lock_path.exists() and not out.with_name(out.name + ".tmp").exists()


# ---- release -------------------------------------------------------------------------------------------------------
def test_the_bundle_tarball_is_deterministic_and_lands_in_the_release_directory(fake_bundle, tmp_path):
    b, _ = fake_bundle
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first = release.bundle_tarball("abc1234", b, tmp_path / "one")
    second = release.bundle_tarball("abc1234", b, tmp_path / "two")
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(first.read_bytes()), mode="r:gz") as tar:
        members = tar.getmembers()
    assert sorted(m.name for m in members) == sorted(f"cautela-abc1234/deploy/demo-bundle/{n}" for n in lock.FILES)
    assert all(m.mode == 0o644 and m.uid == 0 and m.mtime == 0 for m in members)


def test_the_code_tarball_never_carries_the_bundle_or_data():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "deploy/demo-bundle/" in ignored.splitlines()
    allow = (ROOT / "deploy" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    assert "!deploy/demo-bundle/" in allow and "!deploy/demo-bundle.lock.json" in allow and "!data/" not in allow
    assert "data_engineering" in release.PATHS and "data" not in release.PATHS

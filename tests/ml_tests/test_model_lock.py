"""The committed model files, their sha256 lock, and the service's loud fallback to the rule baseline."""

from __future__ import annotations

import logging
import shutil

from agent.orchestrator import disposition as disp
from ml import model_lock


def test_committed_models_match_their_lock():
    assert model_lock.verify() == []


def test_a_changed_model_file_fails_the_lock(tmp_path):
    shutil.copytree(model_lock.COMMITTED_DIR, tmp_path, dirs_exist_ok=True)
    (tmp_path / "systems.pkl").write_bytes(b"not the model")
    assert model_lock.verify(tmp_path) == ["systems.pkl does not match its locked sha256"]


def test_missing_models_fall_back_loudly_and_say_why(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="cautela.disposition"):
        model = disp.load_default(tmp_path)
    assert isinstance(model, disp.RuleDisposition)
    assert model.source.startswith("fallback:") and "model files not found" in model.source
    assert any("running the rule baseline" in r.getMessage() for r in caplog.records)


def test_committed_models_load_when_the_local_training_output_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(disp, "MODELS_DIR", tmp_path / "absent")
    model = disp.load_default()
    assert isinstance(model, disp.LearnedDisposition) and model.source == "ml/models"


def test_a_tampered_committed_copy_is_not_loaded(tmp_path, monkeypatch):
    shutil.copytree(model_lock.COMMITTED_DIR, tmp_path / "committed")
    (tmp_path / "committed" / "learned.pkl").write_bytes(b"tampered")
    monkeypatch.setattr(disp, "MODELS_DIR", tmp_path / "absent")
    monkeypatch.setattr(disp, "COMMITTED_MODELS_DIR", tmp_path / "committed")
    model = disp.load_default()
    assert isinstance(model, disp.RuleDisposition) and "learned.pkl does not match" in model.source

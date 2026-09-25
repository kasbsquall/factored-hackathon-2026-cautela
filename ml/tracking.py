"""MLflow tracking under the local, git-ignored ``./mlruns`` directory.

MLflow 3 puts the plain file store in maintenance mode, so runs go to a SQLite
database at ``mlruns/mlflow.db`` with artifacts in ``mlruns/artifacts``. Browse
with ``uv run mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

EXPERIMENT = "cautela-which-charge"


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, f"{key}."))
        else:
            out[key] = v
    return out


@contextmanager
def run(name: str, params: dict, tracking_dir: Path = Path("mlruns")):
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow

    tracking_dir.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{(tracking_dir / 'mlflow.db').resolve().as_posix()}")
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(EXPERIMENT, artifact_location=(tracking_dir / "artifacts").resolve().as_uri())
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=name) as active:
        mlflow.log_params({k: str(v)[:500] for k, v in flatten(params).items()})
        yield active


def log_metrics(metrics: dict) -> None:
    import mlflow

    clean = {}
    for k, v in flatten(metrics).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
            clean[k.replace("%", "pct").replace(" ", "_")] = float(v)
    mlflow.log_metrics(clean)


def log_artifact(path: Path) -> None:
    import mlflow

    mlflow.log_artifact(str(path))

"""Learned pointwise ranker over the same features as the rule baseline.

A binary classifier scores P(candidate is the charge the customer means). It is
trained on ``train`` match cases (target = 1, others = 0) and no_match cases
(all 0). Ambiguous cases are left out of fitting because their label says
"more than one candidate fits", not which one. Hyperparameters and the model
family are chosen on ``val`` (see ml/train.py).
"""

from __future__ import annotations

import pickle
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from ml.features.pairwise import candidate_features
from ml.rankers.protocol import coerce_features, order


class LearnedRanker:
    def __init__(self, model: Any, name: str = "learned_v1"):
        self.model = model
        self.name = name

    def rank(self, features: Any, candidates: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
        if not candidates:
            return []
        parsed, report = coerce_features(features)
        x = np.asarray(candidate_features(parsed, list(candidates), report), dtype=float)
        scores = self.model.predict_proba(x)[:, 1]
        return order([c["transaction_id"] for c in candidates], scores.tolist(), candidates)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump({"name": self.name, "model": self.model}, fh)

    @classmethod
    def load(cls, path: Path) -> "LearnedRanker":
        with path.open("rb") as fh:  # local artifact written by ml/train.py, never downloaded
            blob = pickle.load(fh)
        return cls(blob["model"], blob["name"])

"""Decide step, learned part: which of the customer's own charges the description fits, if any.

The learned disposition model from ml/ (ranker plus case-level classifier, thresholds fitted on val, business act
floor 0.60) proposes resolve / clarify / escalate. It only proposes: the policy engine decides what is allowed, and
narrow() lets the proposal remove actions or add escalation, never the reverse.

The fitted artifacts live in data/ml/models (git-ignored, written by `uv run python -m ml.train`). When they are
missing, the fixed rule baseline from ml/ is used and every decision says so (`model` names it), so a result is
never attributed to a model that did not run.
"""

from __future__ import annotations

import pickle
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

from ml.decision import K_CLARIFY, FixedRuleDecider
from ml.disposition import cue_fits
from ml.rankers.learned import LearnedRanker
from ml.rankers.rules import RuleRanker

MODELS_DIR = Path(__file__).resolve().parents[2] / "data" / "ml" / "models"
LEARNED_SYSTEM = "learned_ranker_disposition"
_MAP = {"act": "resolve", "clarify": "clarify", "abstain": "escalate"}
DISPUTABLE_STATUSES = frozenset({"Approved", "Pending"})  # the label rule's disputable charges (ml/scenarios/hints.py)
MIN_FIT_CUES = 2  # one cue ("a purchase last week") is too little to name a single charge


@dataclass(frozen=True)
class Disposition:
    decision: Literal["resolve", "clarify", "escalate"]
    confidence: float
    top_k: list[str]
    model: str
    probabilities: dict[str, float] = field(default_factory=dict)
    note: str | None = None


class DispositionModel(Protocol):
    name: str

    def decide(self, text: str, report_date: date, overrides: Mapping[str, Any],
               pool: Sequence[Mapping[str, Any]]) -> Disposition: ...


def ranker_input(text: str, report_date: date, overrides: Mapping[str, Any]) -> dict[str, Any]:
    """What the ml rankers read: text and report date, plus structured values that override the parser."""
    return {"text": text, "report_date": report_date.isoformat(), **overrides}


class LearnedDisposition:
    def __init__(self, ranker: Any, decider: Any) -> None:
        self.ranker, self.decider = ranker, decider
        self.name = f"{LEARNED_SYSTEM}:{decider.name}"

    @classmethod
    def load(cls, models_dir: Path = MODELS_DIR) -> LearnedDisposition:
        with (models_dir / "systems.pkl").open("rb") as fh:  # local artifact written by ml/train.py
            systems = pickle.load(fh)
        _, decider = systems[LEARNED_SYSTEM]
        return cls(LearnedRanker.load(models_dir / "learned.pkl"), decider)

    def decide(self, text: str, report_date: date, overrides: Mapping[str, Any],
               pool: Sequence[Mapping[str, Any]]) -> Disposition:
        inp = ranker_input(text, report_date, overrides)
        candidates = list(pool)
        ranked = self.ranker.rank(inp, candidates) if candidates else []
        if not ranked:
            return Disposition("escalate", 0.0, [], self.name)
        probabilities = {str(k): round(float(v), 4) for k, v in self.decider.proba(inp, candidates, ranked).items()}
        confidence, decided = self.decider.decide_case(inp, candidates, ranked)
        note = None
        if decided["decision"] == "abstain" and max(probabilities, key=probabilities.get) != "no_match":
            # The fitted rule abstains once P(no_match) reaches t_abstain (0.046), even when the classifier puts
            # most of its mass on match or ambiguous. The component metric scores abstain and clarify alike on
            # those cases; in the service they differ: a clarify shows the charges, and only the customer's
            # explicit pick (or "none of these") follows. So the service asks instead of transferring.
            decided, note = {"decision": "clarify", "top_k": [t for t, _ in ranked[:K_CLARIFY]]}, "abstain_to_clarify"
        return Disposition(_MAP[decided["decision"]], round(float(confidence), 4), list(decided["top_k"]),
                           self.name, probabilities, note)


class RuleDisposition:
    """Baseline from ml/: hand-weighted ranker and the fixed clarify rule. It never abstains on a ranking."""

    name = "rules_fixed_baseline"

    def __init__(self) -> None:
        self.ranker, self.decider = RuleRanker(), FixedRuleDecider()

    def decide(self, text: str, report_date: date, overrides: Mapping[str, Any],
               pool: Sequence[Mapping[str, Any]]) -> Disposition:
        inp = ranker_input(text, report_date, overrides)
        candidates = list(pool)
        ranked = self.ranker.rank(inp, candidates) if candidates else []
        if not ranked:
            return Disposition("escalate", 0.0, [], self.name)
        confidence, decided = self.decider.decide_case(inp, candidates, ranked)
        return Disposition(_MAP[decided["decision"]], round(float(confidence), 4), list(decided["top_k"]), self.name)


def non_disputable_fit(text: str, report_date: date, overrides: Mapping[str, Any],
                       pool: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The one charge the description fits on every cue, when that charge moved no money (declined or reversed).

    The disposition models learned "which charge" from labels where only approved or pending debits can be the
    charge a customer means, so a declined charge that fits every cue scores low and the result turns on incidental
    wording. Whether a charge can be disputed is the policy engine's question (SYN-STATUS-002, SYN-STATUS-003), so
    this charge goes to policy directly. None when fewer than MIN_FIT_CUES cues were read, when no charge or more
    than one fits every cue, or when the one that fits is disputable: the model decides those.
    """
    candidates = [dict(t) for t in pool]
    cues, ids = cue_fits(ranker_input(text, report_date, overrides), candidates)
    if len(cues) < MIN_FIT_CUES or len(ids) != 1:
        return None
    tx = next(t for t in candidates if t["transaction_id"] == ids[0])
    return None if tx.get("transaction_status") in DISPUTABLE_STATUSES else tx


def load_default(models_dir: Path = MODELS_DIR) -> DispositionModel:
    """The learned model when its artifacts exist, otherwise the labeled rule baseline."""
    if (models_dir / "systems.pkl").is_file() and (models_dir / "learned.pkl").is_file():
        try:
            return LearnedDisposition.load(models_dir)
        except (OSError, KeyError, pickle.UnpicklingError, AttributeError, ModuleNotFoundError):
            pass
    return RuleDisposition()

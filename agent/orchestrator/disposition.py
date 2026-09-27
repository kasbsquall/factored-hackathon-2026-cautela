"""Decide step, learned part: which of the customer's own charges the description fits, if any.

The learned disposition model from ml/ (ranker plus case-level classifier, thresholds fitted on val, business act
floor 0.60) proposes resolve / clarify / escalate. It only proposes: the policy engine decides what is allowed, and
narrow() lets the proposal remove actions or add escalation, never the reverse.

The fitted artifacts are read from data/ml/models (git-ignored, written by `uv run python -m ml.train`) when they
exist, otherwise from the committed copy in ml/models after its sha256 lock is checked (ml/model_lock.py). When
neither loads, the fixed rule baseline from ml/ runs: `load_default` logs a warning with the reason, the model's
`source` says it is a fallback (GET /health can show it), and every decision names the model that ran.

The decision the service runs is the fitted rule plus `ml.decision.deployed_abstain_rule`: an abstention stands only
when no_match is the most likely class, otherwise the customer is asked to pick. `ml/evaluate_baselines.py` scores
that same decision on val and test, and eval/results_after_fix.json holds it end to end.
"""

from __future__ import annotations

import logging
import pickle
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

from ml.decision import FixedRuleDecider, deployed_abstain_rule
from ml.disposition import _cue_hits, cue_fits
from ml.features.pairwise import candidate_features
from ml.label_rule import LabelRuleDecider
from ml.model_lock import COMMITTED_DIR
from ml.model_lock import verify as verify_committed
from ml.rankers.learned import LearnedRanker
from ml.rankers.protocol import coerce_features
from ml.rankers.rules import RuleRanker

MODELS_DIR = Path(__file__).resolve().parents[2] / "data" / "ml" / "models"
COMMITTED_MODELS_DIR = COMMITTED_DIR  # ml/models, committed with models.lock.json
log = logging.getLogger("cautela.disposition")
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
    def __init__(self, ranker: Any, decider: Any, source: str = "in memory") -> None:
        self.ranker, self.decider, self.source = ranker, decider, source
        self.name = f"{LEARNED_SYSTEM}:{decider.name}"

    @classmethod
    def load(cls, models_dir: Path = MODELS_DIR) -> LearnedDisposition:
        with (models_dir / "systems.pkl").open("rb") as fh:  # artifact written by ml/train.py (or its locked copy)
            systems = pickle.load(fh)
        _, decider = systems[LEARNED_SYSTEM]
        return cls(LearnedRanker.load(models_dir / "learned.pkl"), decider, source=_where(models_dir))

    def decide(self, text: str, report_date: date, overrides: Mapping[str, Any],
               pool: Sequence[Mapping[str, Any]]) -> Disposition:
        inp = ranker_input(text, report_date, overrides)
        candidates = list(pool)
        ranked = self.ranker.rank(inp, candidates) if candidates else []
        if not ranked:
            return Disposition("escalate", 0.0, [], self.name)
        raw = {str(k): float(v) for k, v in self.decider.proba(inp, candidates, ranked).items()}
        confidence, decided = self.decider.decide_case(inp, candidates, ranked)
        # The fitted rule abstains once P(no_match) reaches t_abstain (0.0464), even when the classifier puts most
        # of its mass on match or ambiguous. In the service a clarify shows the charges and only the customer's
        # explicit pick (or "none of these") follows, so it asks instead of transferring unless no_match is the most
        # likely class. ml/evaluate_baselines.py scores this same function as the deployed decision.
        decided, note = deployed_abstain_rule(decided, ranked, raw)
        probabilities = {k: round(v, 4) for k, v in raw.items()}
        return Disposition(_MAP[decided["decision"]], round(float(confidence), 4), list(decided["top_k"]),
                           self.name, probabilities, note)


class RuleDisposition:
    """Baseline from ml/: hand-weighted ranker and the fixed clarify rule. It never abstains on a ranking."""

    name = "rules_fixed_baseline"

    def __init__(self, source: str = "rules baseline, chosen explicitly") -> None:
        self.ranker, self.decider, self.source = RuleRanker(), FixedRuleDecider(), source

    def decide(self, text: str, report_date: date, overrides: Mapping[str, Any],
               pool: Sequence[Mapping[str, Any]]) -> Disposition:
        inp = ranker_input(text, report_date, overrides)
        candidates = list(pool)
        ranked = self.ranker.rank(inp, candidates) if candidates else []
        if not ranked:
            return Disposition("escalate", 0.0, [], self.name)
        confidence, decided = self.decider.decide_case(inp, candidates, ranked)
        return Disposition(_MAP[decided["decision"]], round(float(confidence), 4), list(decided["top_k"]), self.name)


class LabelRuleDisposition(RuleDisposition):
    """Evaluation baseline, never loaded by the service: the scenario label rule on the parsed cues (ml/label_rule.py).

    It orders a clarifying list with the hand-weighted ranker, so nothing in it is fitted.
    """

    name = "label_rule_on_parsed_cues"

    def __init__(self) -> None:
        super().__init__(source="label rule baseline, chosen explicitly")
        self.decider = LabelRuleDecider()


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


def plausible_charges(text: str, report_date: date, overrides: Mapping[str, Any],
                      pool: Sequence[Mapping[str, Any]]) -> list[str]:
    """Ids of the charges the description could mean: each satisfies at least MIN_FIT_CUES of the cues read and
    fails at most one of them (same cue tests and tolerances as the labels, `ml.disposition._cue_hits`), whatever
    its type or status.

    Every cue when two are read; all but one when three or more are, the "near" fit the disposition model already
    counts as a feature (`n_near` in ml/disposition.py). One wrong detail is how a description usually misses its
    charge (an amount in another currency, "early this month" for the last days of the previous one). Empty when
    fewer than MIN_FIT_CUES cues were read: one cue ("about 4,500") is too little to name a charge.
    """
    candidates = [dict(t) for t in pool]
    parsed, report = coerce_features(ranker_input(text, report_date, overrides))
    rows = candidate_features(parsed, candidates, report) if candidates else []
    missed = {}
    for tx, row in zip(candidates, rows):
        hits = list(_cue_hits(row).values())
        if hits.count(True) >= MIN_FIT_CUES and hits.count(False) <= 1:
            missed[tx["transaction_id"]] = hits.count(False)
    return sorted(missed, key=missed.get)  # charges that fit every cue first, then pool order (newest first)


def _where(models_dir: Path) -> str:
    root = Path(__file__).resolve().parents[2]
    try:
        return models_dir.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(models_dir)


def load_default(models_dir: Path | None = None) -> DispositionModel:
    """The learned model, else the rule baseline with a logged warning that says why.

    Without ``models_dir`` it tries data/ml/models (a local ml.train run), then the committed ml/models after its
    sha256 lock passes. The returned model's ``source`` names the directory it came from, or the fallback reason.
    """
    reasons = []
    for directory in ([models_dir] if models_dir is not None else [MODELS_DIR, COMMITTED_MODELS_DIR]):
        if not ((directory / "systems.pkl").is_file() and (directory / "learned.pkl").is_file()):
            reasons.append(f"{_where(directory)}: model files not found")
            continue
        if directory == COMMITTED_MODELS_DIR:
            problems = verify_committed(directory)
            if problems:
                reasons.append(f"{_where(directory)}: {'; '.join(problems)}")
                continue
        try:
            model = LearnedDisposition.load(directory)
        except (OSError, KeyError, pickle.UnpicklingError, AttributeError, ModuleNotFoundError) as exc:
            reasons.append(f"{_where(directory)}: {type(exc).__name__}")
            continue
        log.info("disposition: %s loaded from %s", model.name, model.source)
        return model
    reason = " | ".join(reasons)
    log.warning("disposition: the learned model did not load (%s); running the rule baseline %s",
                reason, RuleDisposition.name)
    return RuleDisposition(source=f"fallback: {reason}")

"""Fit the learned ranker, the calibrators and the decision thresholds.

    uv run python -m ml.train

* model family and hyperparameters: fitted on train, selected by val MRR;
* calibrators: fitted on train (out-of-fold scores for the learned ranker);
* thresholds: fitted on val.

The test split is never read here.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import time

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml import tracking
from ml.data import MODELS_DIR, REPORTS_DIR, data_version, load_cases, ranker_input, record
from ml.decision import DEFAULT_ACT_FLOOR, CalibratedDecider, FixedRuleDecider
from ml.disposition import DispositionDecider
from ml.features.pairwise import FEATURE_NAMES, candidate_features
from ml.metrics import reciprocal_rank
from ml.rankers.learned import LearnedRanker
from ml.rankers.protocol import coerce_features
from ml.rankers.rules import RuleRanker

SEED = 13
MAX_UNSAFE_RATE = 0.01


def candidates_grid() -> dict:
    grid = {}
    for c in (0.1, 1.0, 10.0):
        grid[f"logreg_C{c}"] = lambda c=c: make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=2000))
    for depth in (3, 6):
        for lr in (0.05, 0.1):
            grid[f"hgb_d{depth}_lr{lr}"] = lambda d=depth, lr=lr: HistGradientBoostingClassifier(
                max_depth=d, learning_rate=lr, max_iter=300, l2_regularization=1.0, random_state=SEED)
    return grid


def design_matrix(cases: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rows = candidates of match and no_match cases; ambiguous cases are not used for fitting."""
    xs, ys, groups = [], [], []
    for case in cases:
        if case["label"] == "ambiguous":
            continue
        parsed, report = coerce_features(ranker_input(case))
        rows = candidate_features(parsed, case["candidates"], report)
        for cand, row in zip(case["candidates"], rows):
            xs.append(row)
            ys.append(int(cand["transaction_id"] == case["target_transaction_id"]))
            groups.append(case["customer_ref"])
    return np.asarray(xs, dtype=float), np.asarray(ys), np.asarray(groups)


def rank_all(ranker, cases: list[dict]) -> list[dict]:
    return [record(c, ranker.rank(ranker_input(c), c["candidates"])) for c in cases]


def mrr_on_match(records: list[dict]) -> float:
    m = [r for r in records if r["label"] == "match"]
    return float(np.mean([reciprocal_rank([t for t, _ in r["ranked"]], r["target"]) for r in m]))


def out_of_fold_records(make_model, cases: list[dict], folds: int = 5) -> list[dict]:
    """Train-split rankings where each case is scored by a model that never saw its customer."""
    customers = np.array([c["customer_ref"] for c in cases])
    out: dict[str, dict] = {}
    for fit_idx, score_idx in GroupKFold(n_splits=folds).split(cases, groups=customers):
        x, y, _ = design_matrix([cases[i] for i in fit_idx])
        ranker = LearnedRanker(make_model().fit(x, y))
        for i in score_idx:
            out[cases[i]["case_id"]] = record(cases[i], ranker.rank(ranker_input(cases[i]), cases[i]["candidates"]))
    return [out[c["case_id"]] for c in cases]


def act_floor_setting(cli_value: float | None) -> float:
    """Business floor for acting: CLI, then CAUTELA_ACT_FLOOR, then DEFAULT_ACT_FLOOR. Never fitted."""
    if cli_value is not None:
        return cli_value
    return float(os.environ.get("CAUTELA_ACT_FLOOR", DEFAULT_ACT_FLOOR))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--act-floor", type=float, default=None, help=f"default {DEFAULT_ACT_FLOOR}")
    act_floor = act_floor_setting(ap.parse_args().act_floor)
    t0 = time.time()
    train, val = load_cases("train"), load_cases("val")
    version = data_version()
    x, y, _ = design_matrix(train)
    xv, yv, _ = design_matrix(val)
    selection = {}
    fitted = {}
    for name, make in candidates_grid().items():
        model = make().fit(x, y)
        fitted[name] = model
        selection[name] = {"val_mrr": round(mrr_on_match(rank_all(LearnedRanker(model), val)), 4),
                           "val_log_loss": round(float(log_loss(yv, model.predict_proba(xv)[:, 1])), 4)}
    # primary: MRR on val match cases; tie-break: candidate-level log loss (lower is better)
    best = max(selection, key=lambda k: (selection[k]["val_mrr"], -selection[k]["val_log_loss"], k))
    learned = LearnedRanker(fitted[best], name=f"learned_{best}")
    rules = RuleRanker()

    oof = out_of_fold_records(candidates_grid()[best], train)
    rules_train, rules_val, learned_val = rank_all(rules, train), rank_all(rules, val), rank_all(learned, val)
    rules_tuned, info_rules = CalibratedDecider.fit(rules_train, rules_val, MAX_UNSAFE_RATE)
    ranker_only, info_ranker_only = CalibratedDecider.fit(oof, learned_val, MAX_UNSAFE_RATE)
    by_id = {r["case_id"]: r["ranked"] for r in oof}
    val_ranked = {r["case_id"]: r["ranked"] for r in learned_val}
    disposition, info_disp = DispositionDecider.fit(
        [(c, ranker_input(c), by_id[c["case_id"]]) for c in train],
        [(c, ranker_input(c), val_ranked[c["case_id"]]) for c in val], MAX_UNSAFE_RATE)

    systems = {  # name -> (ranker key, decider); evaluate.py runs exactly these on test
        "rules_fixed": ("rules", FixedRuleDecider()),
        "rules_tuned": ("rules", rules_tuned.with_floor(act_floor)),
        "learned_ranker_calibrated": ("learned", ranker_only.with_floor(act_floor)),
        "learned_ranker_disposition": ("learned", disposition.with_floor(act_floor)),
    }
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    learned.save(MODELS_DIR / "learned.pkl")
    with (MODELS_DIR / "systems.pkl").open("wb") as fh:
        pickle.dump(systems, fh)

    summary = {
        "data_version": version, "seed": SEED, "features": FEATURE_NAMES,
        "train_rows": int(len(y)), "train_positive_rows": int(y.sum()),
        "train_cases": len(train), "val_cases": len(val),
        "ranker_model_selection": selection, "selected_ranker_model": best,
        "systems": {name: {"ranker": rk, "decider": dec.name, **dec.params()} for name, (rk, dec) in systems.items()},
        "val_fit": {"rules_tuned": info_rules, "learned_ranker_calibrated": info_ranker_only,
                    "learned_ranker_disposition": info_disp},
        "max_unsafe_rate": MAX_UNSAFE_RATE, "act_floor": act_floor, "seconds": round(time.time() - t0, 1),
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "fitted.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    with tracking.run("train", {"data_version": version, "selected_ranker_model": best, "seed": SEED,
                                "disposition_model": disposition.model_name,
                                "max_unsafe_rate": MAX_UNSAFE_RATE, "act_floor": act_floor, "features": ",".join(FEATURE_NAMES)}):
        tracking.log_metrics({"selection": selection, "val_fit": summary["val_fit"]})
        tracking.log_artifact(out)
    print(json.dumps({"selected_ranker_model": best, "val_fit": summary["val_fit"]}, indent=2))


if __name__ == "__main__":
    main()

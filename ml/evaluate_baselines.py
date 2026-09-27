"""Two decisions that the original component evaluation did not score, on val and on test.

    uv run python -m ml.evaluate_baselines     # -> ml/reports/baselines_val.json, baselines_test.json, MLflow runs

* ``label_rule``: the scenario label rule applied directly to the parsed text (``ml/label_rule.py``). The learned
  disposition model gets the same rule as features (``n_full``, ``n_near``, ``top1_full``), so this is the baseline
  that says how much of its result the classifier adds.
* ``learned_deployed``: the decision the service runs (``ml.decision.deployed_abstain_rule`` after the fitted rule).

Both are scored next to ``rules_tuned`` (the baseline of ``ml/evaluate.py``) and ``learned_ranker_disposition`` (the
fitted rule that ``results.json`` and ``results_fresh.json`` report), on the same cases, with the metric functions of
``ml/analysis.py``. Nothing is fitted or tuned here: the label rule has no parameter, and the other systems are the
artifacts of ``ml/train.py``. Val is where the systems were fitted and chosen; test was already used for error
analysis. Neither number is a fresh estimate, and ``test_fresh`` is never read by this script.
"""

from __future__ import annotations

import json
import pickle
from collections import Counter

from ml import tracking
from ml.analysis import correct_rate, paired_difference, safe_auto_rate, summarize, unsafe_rate
from ml.data import MODELS_DIR, REPORTS_DIR, data_version, load_cases
from ml.disposition import DeployedDecider
from ml.evaluate import run_system
from ml.label_rule import LabelRuleDecider
from ml.rankers.learned import LearnedRanker
from ml.rankers.rules import RuleRanker

SPLITS = ("val", "test")  # never test_fresh: it is spent and evaluated once by ml/evaluate.py
PAIRS = (("label_rule", "learned_ranker_disposition"), ("label_rule", "learned_deployed"),
         ("learned_ranker_disposition", "learned_deployed"), ("rules_tuned", "label_rule"))
METRICS = {"correct_decision_rate": correct_rate, "unsafe_rate": unsafe_rate,
           "safe_automated_resolution_rate": safe_auto_rate}


def systems() -> dict:
    with (MODELS_DIR / "systems.pkl").open("rb") as fh:  # artifact written by ml/train.py
        fitted = pickle.load(fh)
    rankers = {"rules": RuleRanker(), "learned": LearnedRanker.load(MODELS_DIR / "learned.pkl")}
    disposition = fitted["learned_ranker_disposition"][1]
    return {"rules_tuned": (rankers["rules"], fitted["rules_tuned"][1]),
            "label_rule": (rankers["rules"], LabelRuleDecider()),
            "learned_ranker_disposition": (rankers["learned"], disposition),
            "learned_deployed": (rankers["learned"], DeployedDecider(disposition))}


def agreement(a: list[dict], b: list[dict]) -> dict:
    """Case by case: same decision on the same charges, and the outcomes where the two differ."""
    by_case = {r["case_id"]: r for r in a}
    same = diff = 0
    outcomes: Counter = Counter()
    decisions: Counter = Counter()
    for rb in b:
        ra = by_case[rb["case_id"]]
        if ra["decision"] == rb["decision"] and ra["top_k"] == rb["top_k"]:
            same += 1
            continue
        diff += 1
        outcomes[f"{ra['outcome']} -> {rb['outcome']}"] += 1
        decisions[f"{ra['decision']} -> {rb['decision']}"] += 1
    return {"same_decision": {"count": same, "denominator": len(b)}, "differ": diff,
            "outcome_changes": dict(outcomes.most_common()), "decision_changes": dict(decisions.most_common())}


def evaluate_split(split: str, version: str, systems_: dict) -> dict:
    cases = load_cases(split)
    rows = {name: run_system(ranker, decider, cases) for name, (ranker, decider) in systems_.items()}
    out: dict = {"generated_by": "ml/evaluate_baselines.py", "data_version": version, "split": split,
                 "note": "val was used to fit and choose the learned systems and test for error analysis; neither "
                         "is a fresh estimate. Nothing was fitted or tuned by this script.",
                 "systems": {}, "paired_differences": {}, "agreement": {}}
    for name, (ranker, decider) in systems_.items():
        summary = summarize(rows[name])
        n_match = summary["n_by_label"].get("match", 0)
        summary["safe_automated_resolution_ceiling"] = {"count": n_match, "denominator": summary["n_cases"],
                                                        "note": "only a correct act on a match case counts"}
        out["systems"][name] = {"ranker": ranker.name, "decider": decider.name, "decider_params": decider.params(),
                                "summary": summary}
    for a, b in PAIRS:
        out["paired_differences"][f"{b}_minus_{a}"] = {m: paired_difference(rows[a], rows[b], fn)
                                                       for m, fn in METRICS.items()}
        out["agreement"][f"{a}_vs_{b}"] = agreement(rows[a], rows[b])
    return out


def main() -> None:
    version = data_version()
    fitted = json.loads((REPORTS_DIR / "fitted.json").read_text(encoding="utf-8"))
    if fitted["data_version"] != version:
        raise SystemExit("fitted artifacts were trained on another data version; run ml.train first")
    systems_ = systems()
    for split in SPLITS:
        result = evaluate_split(split, version, systems_)
        path = REPORTS_DIR / f"baselines_{split}.json"
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        for name, body in result["systems"].items():
            with tracking.run(f"evaluate_baselines:{split}:{name}", {
                    "system": name, "ranker": body["ranker"], "decider": body["decider"], "data_version": version,
                    "split": split, "prompt_version": "n/a"}):
                tracking.log_metrics({"summary": body["summary"]})
                tracking.log_artifact(path)
        print(json.dumps({split: {n: {k: b["summary"][k]["value"] for k in ("correct_decision_rate", "unsafe_rate",
                                                                             "safe_automated_resolution_rate", "ece")}
                                  | {"unsafe": b["summary"]["unsafe"]["count"]}
                                  for n, b in result["systems"].items()}}, indent=1))


if __name__ == "__main__":
    main()

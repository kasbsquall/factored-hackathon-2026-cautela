from ml.probe_llm import judge, rate


def _case(label, target=None, consistent=()):
    return {"label": label, "target_transaction_id": target, "consistent_ids": list(consistent)}


def test_judge_scores_match_on_the_target_only():
    assert judge(_case("match", "T1"), [("T1", 0.9), ("T2", 0.1)])["top1_hit"] is True
    assert judge(_case("match", "T1"), [("T2", 0.9), ("T1", 0.1)])["top1_hit"] is False


def test_judge_ambiguous_accepts_any_consistent_candidate_and_reports_margin():
    row = judge(_case("ambiguous", None, ["T1", "T2"]), [("T2", 0.6), ("T1", 0.5)])
    assert row["top1_consistent"] is True and row["top1_hit"] is None and row["margin"] == 0.1


def test_judge_no_match_keeps_the_top_score_to_expose_overconfidence():
    assert judge(_case("no_match"), [("T1", 0.8)])["no_match_top_score"] == 0.8


def test_rate_ignores_rows_without_the_metric():
    assert rate([{"k": True}, {"k": False}, {"k": None}], "k") == {"n": 2, "rate": 0.5}
    assert rate([], "k") == {"n": 0, "rate": None}

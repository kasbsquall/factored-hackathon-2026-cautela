"""What satisfaction depends on: can a calm customer be told from an anxious one before the contact?

The question came from a judge: could the service tell who wants a call from who is happy with a receipt, and
tailor the follow-up. Four parts, each on the organizer warehouse opened read-only:

1. How satisfaction is measured (coverage and scale of each score).
2. Dispute resolution_satisfaction by case factor, with 95% intervals (mean +- 1.96 SE).
3. Whether customers have a habitual contact channel, and whether satisfaction is higher when a contact or a
   survey uses it (habit computed only from contacts before the event, at least MIN_PRIOR of them).
4. Whether low satisfaction is predictable: logistic regressions split by customer, each compared with refits on
   shuffled labels.

`python -m data_analytics.satisfaction` runs it and writes data_analytics/reports/satisfaction.md.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

SEED = 0
N_PERMUTATIONS = 100
N_BOOTSTRAP = 200
MIN_PRIOR = 2  # prior contacts needed before a customer is given a habit
LOW_CSAT = 2  # CSAT (1-4) and resolution_satisfaction (1-5) at or below this count as low

CC_GROUP = """CASE channel WHEN 'Phone' THEN 'voice' WHEN 'WhatsApp' THEN 'messaging'
              WHEN 'Web Chat' THEN 'messaging' ELSE 'digital' END"""  # Email, App, Web
HABIT = f"""CASE WHEN n_prior < {MIN_PRIOR} THEN 'unknown' WHEN p_voice >= 0.5 * n_prior THEN 'voice'
                 WHEN p_msg > p_dig THEN 'messaging' ELSE 'digital' END"""
AMOUNT_USD = """CASE WHEN f.currency = 'USD' THEN f.claimed_amount
                     WHEN f.currency IS NOT NULL THEN f.claimed_amount * r.exchange_rate END"""
FX_JOIN = """LEFT JOIN silver.daily_exchange_rates r ON r.source_currency = f.currency
             AND r.target_currency = 'USD' AND r.date = CAST(f.creation_date AS DATE)"""
DISPUTE_FACTORS = ["reception_channel", "customer_segment", "customer_country", "is_repeat_complainer",
                   "sla_breached", "resolution_days_bucket", "amount_usd_bucket", "compensated", "priority"]


def _df(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


def mean_ci(frame: pd.DataFrame, by: str | list[str], y: str) -> pd.DataFrame:
    g = frame.groupby(by, dropna=False)[y].agg(["count", "mean", "std"]).reset_index()
    se = g["std"] / np.sqrt(g["count"])
    g["ci_lo"], g["ci_hi"] = g["mean"] - 1.96 * se, g["mean"] + 1.96 * se
    return g.drop(columns="std").rename(columns={"count": "n"}).round(3)


def diff_ci(a: pd.Series, b: pd.Series) -> dict:
    d = a.mean() - b.mean()
    se = np.sqrt(a.var() / len(a) + b.var() / len(b))
    return {"diff": round(d, 3), "ci": [round(d - 1.96 * se, 3), round(d + 1.96 * se, 3)],
            "n_matched": len(a), "n_not_matched": len(b)}


def measures(con: duckdb.DuckDBPyConnection) -> dict:
    scores = _df(con, """
        SELECT resolution_satisfaction AS score,
               count(*) FILTER (WHERE is_unrecognized_charge) AS cargo_no_reconocido, count(*) AS all_complaints
        FROM gold.complaint_facts WHERE resolution_satisfaction IS NOT NULL GROUP BY 1 ORDER BY 1""")
    for c in ["cargo_no_reconocido", "all_complaints"]:
        scores[c + "_pct"] = (100 * scores[c] / scores[c].sum()).round(1)
    return {
        "coverage": _df(con, """
            SELECT status, count(*) AS complaints, count(resolution_satisfaction) AS scored
            FROM gold.complaint_facts GROUP BY 1 ORDER BY 2 DESC, 1"""),
        "scores": scores,
        "surveys": _df(con, """
            SELECT survey_type, count(*) AS surveys, min(main_score) AS min_score, max(main_score) AS max_score,
                   count(nps_category) AS with_nps_category
            FROM silver.satisfaction_surveys GROUP BY 1 ORDER BY 1"""),
        "csat_by_reason": _df(con, f"""
            SELECT i.reason_category, count(*) AS csat_surveys, round(avg(s.main_score), 3) AS mean_csat,
                   round(avg((s.main_score <= {LOW_CSAT})::INT), 3) AS share_low
            FROM silver.satisfaction_surveys s JOIN silver.call_center_interactions i USING (interaction_id)
            WHERE s.survey_type = 'CSAT' GROUP BY 1 ORDER BY 1"""),
    }


def dispute_factors(con: duckdb.DuckDBPyConnection) -> dict:
    disp = _df(con, f"""
        SELECT f.*, {AMOUNT_USD} AS amount_usd FROM gold.complaint_facts f {FX_JOIN}
        WHERE f.is_unrecognized_charge AND f.resolution_satisfaction IS NOT NULL ORDER BY f.complaint_id""")
    disp["resolution_days_bucket"] = pd.cut(disp["resolution_days"], [-1, 7, 15, 30, 10_000],
                                            labels=["0-7", "8-15", "16-30", "31+"]).astype(str)
    disp["amount_usd_bucket"] = pd.cut(disp["amount_usd"].astype(float), [-1, 50, 200, 1000, 1e12],
                                       labels=["<=50", "50-200", "200-1000", ">1000"]).astype(str)
    disp["compensated"] = disp["compensation_granted"].fillna(0).astype(float) > 0
    return {"n": len(disp), "mean": round(disp.resolution_satisfaction.mean(), 3),
            "by": {c: mean_ci(disp, c, "resolution_satisfaction") for c in DISPUTE_FACTORS}}


def _contacts_table(con: duckdb.DuckDBPyConnection) -> None:
    """Temp table `ix`: every contact with the counts of the customer's earlier contacts by channel group."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE ix AS
        SELECT interaction_id, customer_id, interaction_date, channel, {CC_GROUP} AS grp, detected_sentiment,
               sentiment_score, reason_category, wait_time_seconds, duration_seconds, was_resolved, was_escalated,
               count(*) OVER w AS n_prior,
               coalesce(sum(({CC_GROUP} = 'voice')::INT) OVER w, 0) AS p_voice,
               coalesce(sum(({CC_GROUP} = 'messaging')::INT) OVER w, 0) AS p_msg,
               coalesce(sum(({CC_GROUP} = 'digital')::INT) OVER w, 0) AS p_dig,
               coalesce(sum((detected_sentiment IN ('Negative', 'Very Negative'))::INT) OVER w, 0) AS p_neg,
               avg(sentiment_score) OVER w AS prior_sent_mean,
               lag(detected_sentiment IN ('Negative', 'Very Negative')) OVER o AS prev_neg,
               lag({CC_GROUP}) OVER o AS prev_grp
        FROM silver.call_center_interactions
        WINDOW o AS (PARTITION BY customer_id ORDER BY interaction_date, interaction_id),
               w AS (PARTITION BY customer_id ORDER BY interaction_date, interaction_id
                     ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)""")


def channel_habit(con: duckdb.DuckDBPyConnection) -> dict:
    p = _df(con, """
        WITH cc AS (SELECT customer_id, count(*) AS n, sum((grp = 'voice')::INT) AS v,
                           sum((grp = 'messaging')::INT) AS m, sum((grp = 'digital')::INT) AS d
                    FROM ix GROUP BY 1)
        SELECT c.customer_id, coalesce(n, 0) AS n, coalesce(v, 0) AS v, coalesce(m, 0) AS m, coalesce(d, 0) AS d
        FROM silver.customers c LEFT JOIN cc USING (customer_id) ORDER BY c.customer_id""")
    p["preference"] = np.select([p.n < 3, p.v >= 0.5 * p.n, p.m > p.d],
                                ["low-contact (<3 contacts)", "voice-first", "chat/messaging-first"],
                                "app/web/email-first")
    prefs = p.groupby("preference").size().reset_index(name="customers").sort_values("customers", ascending=False)
    prefs["pct"] = (100 * prefs.customers / prefs.customers.sum()).round(1)
    many = p[p.n >= 5]
    share = many.v / many.n
    pooled = many.v.sum() / many.n.sum()
    expected = (pooled * (1 - pooled) / many.n).mean()
    return {
        "contacts_by_group": _df(con, "SELECT grp, count(*) AS contacts FROM ix GROUP BY 1 ORDER BY 2 DESC"),
        "preferences": prefs.reset_index(drop=True),
        "customers": len(p),
        "dispersion": {"customers": len(many), "pooled_phone_share": round(pooled, 3),
                       "observed_variance": round(share.var(), 5), "binomial_variance": round(expected, 5),
                       "ratio": round(share.var() / expected, 2)},
        "transitions": _df(con, """
            SELECT prev_grp, count(*) AS contacts, round(avg((grp = 'voice')::INT), 4) AS p_voice,
                   round(avg((grp = 'messaging')::INT), 4) AS p_messaging,
                   round(avg((grp = 'digital')::INT), 4) AS p_digital
            FROM ix WHERE prev_grp IS NOT NULL GROUP BY 1 ORDER BY 1"""),
    }


def complaint_match(con: duckdb.DuckDBPyConnection) -> dict:
    cm = _df(con, f"""
        WITH c AS (SELECT complaint_id, customer_id, creation_date, resolution_satisfaction, is_unrecognized_charge,
                          CASE reception_channel WHEN 'Call Center' THEN 'voice' WHEN 'App' THEN 'digital'
                               WHEN 'Web' THEN 'digital' WHEN 'Email' THEN 'digital' END AS cgrp
                   FROM gold.complaint_facts WHERE resolution_satisfaction IS NOT NULL),
             h AS (SELECT c.complaint_id, count(i.interaction_id) AS n_prior,
                          coalesce(sum((i.grp = 'voice')::INT), 0) AS p_voice,
                          coalesce(sum((i.grp = 'messaging')::INT), 0) AS p_msg,
                          coalesce(sum((i.grp = 'digital')::INT), 0) AS p_dig
                   FROM c LEFT JOIN ix i ON i.customer_id = c.customer_id AND i.interaction_date < c.creation_date
                   GROUP BY 1)
        SELECT c.*, {HABIT} AS habit FROM c JOIN h USING (complaint_id) ORDER BY complaint_id""")
    cm = cm[cm.cgrp.notna() & (cm.habit != "unknown")].copy()
    cm["matched"] = cm.cgrp == cm.habit
    d = cm[cm.is_unrecognized_charge]
    y = "resolution_satisfaction"
    return {"all": diff_ci(cm[cm.matched][y], cm[~cm.matched][y]),
            "disputes": diff_ci(d[d.matched][y], d[~d.matched][y])}


def surveys(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """CSAT surveys with features known before the contact (prior contacts, CSAT, complaints, digital events)."""
    con.execute("""CREATE OR REPLACE TEMP TABLE sv AS
        SELECT s.survey_id, s.main_score, s.send_channel, i.*,
               CASE s.send_channel WHEN 'IVR' THEN 'voice' WHEN 'SMS' THEN 'messaging' ELSE 'digital' END AS sgrp
        FROM silver.satisfaction_surveys s JOIN ix i USING (interaction_id) WHERE s.survey_type = 'CSAT'""")
    sv = _df(con, f"""
        WITH prior_csat AS (
            SELECT survey_id, count(*) OVER w AS n_prior_csat, avg(main_score) OVER w AS prior_csat_mean
            FROM sv WINDOW w AS (PARTITION BY customer_id ORDER BY interaction_date, interaction_id
                                 ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)),
             prior_cmp AS (
            SELECT sv.survey_id, count(c.complaint_id) AS n_prior_complaints
            FROM sv LEFT JOIN gold.complaint_facts c ON c.customer_id = sv.customer_id
                                                   AND c.creation_date < sv.interaction_date GROUP BY 1),
             prior_dig AS (
            SELECT sv.survey_id, count(e.event_id) AS n_prior_digital_90d
            FROM sv LEFT JOIN silver.digital_events e ON e.customer_id = sv.customer_id
                 AND e.event_date < sv.interaction_date AND e.event_date >= sv.interaction_date - INTERVAL 90 DAY
            GROUP BY 1)
        SELECT sv.*, {HABIT} AS habit, cu.segment, cu.country, pc.n_prior_csat, pc.prior_csat_mean,
               pm.n_prior_complaints, pd.n_prior_digital_90d
        FROM sv JOIN prior_csat pc USING (survey_id) JOIN prior_cmp pm USING (survey_id)
        JOIN prior_dig pd USING (survey_id) JOIN silver.customers cu USING (customer_id) ORDER BY survey_id""")
    sv["prior_neg_share"] = sv.p_neg / sv.n_prior.replace(0, np.nan)
    sv["low"] = (sv.main_score <= LOW_CSAT).astype(int)
    return sv


def survey_match(sv: pd.DataFrame) -> dict:
    known = sv[sv.habit != "unknown"]
    contact, send = known.grp == known.habit, known.sgrp == known.habit
    return {"n": len(sv), "with_habit": len(known), "min_prior": MIN_PRIOR,
            "contact_channel": diff_ci(known[contact].main_score, known[~contact].main_score),
            "send_channel": diff_ci(known[send].main_score, known[~send].main_score),
            "send_within_habit": {h: diff_ci(known[send & (known.habit == h)].main_score,
                                             known[~send & (known.habit == h)].main_score)
                                  for h in ["voice", "messaging", "digital"]}}


def fit_eval(frame: pd.DataFrame, y: np.ndarray, num: list[str], cat: list[str]) -> dict:
    """Logistic regression, 70/30 split by customer; AUC with a bootstrap interval and a shuffled-label baseline."""
    rng = np.random.default_rng(SEED)
    x = frame[num + cat].copy()
    for c in num:
        x[c + "_missing"] = x[c].isna().astype(int)
        x[c] = x[c].astype(float).fillna(x[c].astype(float).median())
    nums = num + [c + "_missing" for c in num]
    x[cat] = x[cat].astype(str)
    tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=SEED)
                  .split(x, y, groups=frame["customer_id"]))

    def model():
        pre = ColumnTransformer([("n", StandardScaler(), nums), ("c", OneHotEncoder(handle_unknown="ignore"), cat)])
        return make_pipeline(pre, LogisticRegression(max_iter=1000))

    p_te = model().fit(x.iloc[tr], y[tr]).predict_proba(x.iloc[te])[:, 1]
    boot = [roc_auc_score(y[te][ix], p_te[ix]) for ix in
            (rng.integers(0, len(te), len(te)) for _ in range(N_BOOTSTRAP)) if y[te][ix].min() != y[te][ix].max()]
    perm = [roc_auc_score(y[te], model().fit(x.iloc[tr], rng.permutation(y[tr])).predict_proba(x.iloc[te])[:, 1])
            for _ in range(N_PERMUTATIONS)]
    return {"n": len(y), "train": len(tr), "test": len(te), "positive_rate": round(float(y.mean()), 3),
            "auc": round(roc_auc_score(y[te], p_te), 3),
            "auc_ci": [round(np.percentile(boot, 2.5), 3), round(np.percentile(boot, 97.5), 3)],
            "shuffled_mean": round(float(np.mean(perm)), 3), "shuffled_p95": round(float(np.percentile(perm, 95)), 3)}


def complaints_model_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return _df(con, f"""
        WITH c AS (SELECT f.*, {AMOUNT_USD} AS amount_usd FROM gold.complaint_facts f {FX_JOIN}
                   WHERE f.resolution_satisfaction IS NOT NULL)
        SELECT c.complaint_id, c.customer_id, c.resolution_satisfaction, c.is_repeat_complainer::INT AS repeat,
               c.prior_complaint_90d::INT AS prior_complaint_90d, ln(1 + c.amount_usd) AS log_amount_usd,
               c.customer_segment, c.customer_country, c.priority, c.reception_channel,
               c.is_unrecognized_charge::INT AS dispute, count(i.interaction_id) AS n_prior_contacts,
               avg((i.detected_sentiment IN ('Negative', 'Very Negative'))::INT) AS prior_neg_share,
               avg(i.sentiment_score) AS prior_sent_mean
        FROM c LEFT JOIN ix i ON i.customer_id = c.customer_id AND i.interaction_date < c.creation_date
        GROUP BY ALL ORDER BY c.complaint_id""")


def predictability(con: duckdb.DuckDBPyConnection, sv: pd.DataFrame) -> dict:
    ca = complaints_model_frame(con)
    pre_num = ["n_prior", "prior_neg_share", "prior_sent_mean", "n_prior_csat", "prior_csat_mean",
               "n_prior_complaints", "n_prior_digital_90d"]
    pre_cat = ["segment", "country", "channel", "reason_category", "habit"]
    yb = sv.low.to_numpy()
    models = {
        "A. Complaint score <= 2, pre-contact features": fit_eval(
            ca, (ca.resolution_satisfaction <= LOW_CSAT).to_numpy().astype(int),
            ["repeat", "prior_complaint_90d", "log_amount_usd", "dispute", "n_prior_contacts", "prior_neg_share",
             "prior_sent_mean"], ["customer_segment", "customer_country", "priority", "reception_channel"]),
        "B2. CSAT <= 2, customer history only (no contact reason, no channel)": fit_eval(
            sv, yb, pre_num, ["segment", "country", "habit"]),
        "B3. CSAT <= 2, contact reason only": fit_eval(sv, yb, [], ["reason_category"]),
        "B. CSAT <= 2, history plus contact reason and channel": fit_eval(sv, yb, pre_num, pre_cat),
        "C. CSAT <= 2, B plus sentiment, wait, duration and resolution of the contact": fit_eval(
            sv, yb, pre_num + ["sentiment_score", "wait_time_seconds", "duration_seconds"],
            pre_cat + ["detected_sentiment", "was_resolved", "was_escalated"]),
    }
    prior = pd.cut(sv.prior_csat_mean, [0, 2, 3, 4], labels=["<=2", "2-3", "3-4"]).astype(str)
    return {
        "sentiment_persistence": _df(con, """
            SELECT prev_neg, count(*) AS contacts,
                   round(avg((detected_sentiment IN ('Negative', 'Very Negative'))::INT), 4) AS p_negative
            FROM ix WHERE prev_neg IS NOT NULL GROUP BY 1 ORDER BY 1"""),
        "models": models,
        "low_by_prior_csat": mean_ci(sv.assign(prior=prior), "prior", "low"),
        "low_by_sentiment": mean_ci(sv, "detected_sentiment", "low"),
        "low_by_resolution": mean_ci(sv, "was_resolved", "low"),
    }


def analyse(con: duckdb.DuckDBPyConnection) -> dict:
    _contacts_table(con)
    sv = surveys(con)
    return {"measures": measures(con), "disputes": dispute_factors(con), "habit": channel_habit(con),
            "complaint_match": complaint_match(con), "survey_match": survey_match(sv),
            "predictability": predictability(con, sv)}

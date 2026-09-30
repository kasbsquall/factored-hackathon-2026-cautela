"""Customer satisfaction: what the supplied surveys can and cannot say about the workflow.

Two sources. `complaints.resolution_satisfaction` scores a complaint's resolution (gold.complaint_facts). The
`satisfaction_surveys` table links to contact-center interactions through interaction_id, never to a complaint, so
survey CSAT can be read by contact reason and by first-contact resolution, not by dispute.

Run as a module it writes reports/satisfaction.md, the analysis of what satisfaction depends on (see `main`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from data_analytics.figures import one, rows
from data_analytics.fmt import table
from eval.paths import REAL_WAREHOUSE

RESOLUTION_DAY_BUCKETS = "CASE WHEN resolution_days <= 7 THEN '0-7' WHEN resolution_days <= 15 THEN '8-15' " \
                         "WHEN resolution_days <= 30 THEN '16-30' ELSE '31+' END"


def complaint_satisfaction(con: duckdb.DuckDBPyConnection) -> dict:
    by_type = rows(con, """
        SELECT is_unrecognized_charge, count(*) AS complaints, count(resolution_satisfaction) AS with_score,
               round(avg(resolution_satisfaction), 3) AS mean_score, min(resolution_satisfaction) AS min_score,
               max(resolution_satisfaction) AS max_score
        FROM gold.complaint_facts GROUP BY 1 ORDER BY 1""")
    statuses = rows(con, """
        SELECT status, count(resolution_satisfaction) AS with_score FROM gold.complaint_facts
        WHERE resolution_satisfaction IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1""")
    dist = rows(con, """
        SELECT resolution_satisfaction AS score, count(*) AS complaints FROM gold.complaint_facts
        WHERE is_unrecognized_charge AND resolution_satisfaction IS NOT NULL GROUP BY 1 ORDER BY 1""")
    by_days = rows(con, f"""
        SELECT {RESOLUTION_DAY_BUCKETS} AS resolution_days, count(*) AS with_score,
               round(avg(resolution_satisfaction), 3) AS mean_score
        FROM gold.complaint_facts WHERE resolution_satisfaction IS NOT NULL AND resolution_days IS NOT NULL
        GROUP BY 1 ORDER BY min(resolution_days)""")
    return {"by_is_dispute": by_type, "statuses_with_score": statuses, "dispute_score_distribution": dist,
            "all_complaints_by_resolution_days": by_days}


def survey_csat(con: duckdb.DuckDBPyConnection) -> dict:
    links = one(con, """
        SELECT count(*) AS surveys, count(i.interaction_id) AS linked_to_interaction
        FROM silver.satisfaction_surveys s LEFT JOIN silver.call_center_interactions i USING (interaction_id)""")
    base = """FROM silver.satisfaction_surveys s JOIN silver.call_center_interactions i USING (interaction_id)
              WHERE s.survey_type = 'CSAT'"""
    scale = one(con, f"SELECT count(*) AS surveys, min(s.main_score) AS min_score, max(s.main_score) AS max_score "
                     f"{base}")
    by_resolved = rows(con, f"""
        SELECT i.was_resolved, count(*) AS surveys, round(avg(s.main_score), 3) AS mean_score {base}
        GROUP BY 1 ORDER BY 1""")
    by_reason = rows(con, f"""
        SELECT i.reason_category, count(*) AS surveys, round(avg(s.main_score), 3) AS mean_score,
               round(avg(s.main_score) FILTER (WHERE i.was_resolved), 3) AS mean_resolved,
               round(avg(s.main_score) FILTER (WHERE NOT i.was_resolved), 3) AS mean_unresolved,
               round(avg(i.was_resolved::INT), 4) AS fcr_of_surveyed
        {base} GROUP BY 1 ORDER BY mean_score, 1""")
    return {"links": links, "scale": scale, "by_first_contact_resolution": by_resolved, "by_reason": by_reason}


def satisfaction(con: duckdb.DuckDBPyConnection) -> dict:
    return {"complaints": complaint_satisfaction(con), "surveys": survey_csat(con)}


# ---------------------------------------------------------------------------------------------------------------
# What satisfaction depends on (data_analytics/satisfaction_drivers.py), written to reports/satisfaction.md.
#
#     uv run python -m data_analytics.satisfaction                      # organizer warehouse, read-only
#     uv run python -m data_analytics.satisfaction --warehouse data/warehouse.duckdb --out <file>   # fixture

REPORT_PATH = Path(__file__).resolve().parent / "reports" / "satisfaction.md"
DRIVERS_B2 = "B2. CSAT <= 2, customer history only (no contact reason, no channel)"


def _cell(v) -> str:
    if isinstance(v, float):
        return "n/a" if v != v else f"{v:,.3f}".rstrip("0").rstrip(".")
    return f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else str(v)


def _frame(frame) -> str:
    return table([str(c) for c in frame.columns], [[_cell(v) for v in row] for row in frame.itertuples(index=False)])


def _diff(d: dict) -> list[str]:
    return [f"{d['diff']:+.3f} [{d['ci'][0]:+.3f}, {d['ci'][1]:+.3f}]", f"{d['n_matched']:,} vs {d['n_not_matched']:,}"]


def _share(frame, key) -> tuple[float, int]:
    row = frame[frame.iloc[:, 0] == key].iloc[0]
    return float(row["mean"]), int(row["n"])


def _summary(r: dict) -> list[str]:
    pr, disp = r["predictability"], r["habit"]["dispersion"]
    b2 = pr["models"][DRIVERS_B2]
    unres, n_unres = _share(pr["low_by_resolution"], False)
    res, n_res = _share(pr["low_by_resolution"], True)
    return [
        "## Answer",
        "",
        "* Nothing about the customer predicts low satisfaction. A model on customer history only (prior contacts, "
        "their sentiment, prior CSAT, complaints, digital activity, segment, country, channel habit) reaches test "
        f"ROC-AUC {b2['auc']:.3f} on {b2['n']:,} CSAT surveys, against {b2['shuffled_mean']:.3f} with shuffled "
        "labels.",
        f"* There is no channel habit to match. Among {disp['customers']:,} customers with 5 or more contacts, the "
        f"per-customer phone share varies {disp['ratio']:.2f} times what random channel choice would give, and "
        "satisfaction is the same whether or not a contact or a survey uses the customer's usual channel "
        "(section 3).",
        f"* Resolution is what moves satisfaction: CSAT is 2 or lower (of 4) in {100 * unres:.0f}% of unresolved "
        f"contacts (n={n_unres:,}) and in {100 * res:.0f}% of resolved ones (n={n_res:,}).",
        "",
        "Product decision taken from this: Cautela does not label customers as calm or anxious. After filing, every "
        "customer chooses the follow-up: keep the receipt, or have a person take over the case, who receives the "
        "case number and the checked facts.",
        "",
    ]


def _details(r: dict) -> list[str]:
    m, dsp, hab, pr, sm = r["measures"], r["disputes"], r["habit"], r["predictability"], r["survey_match"]
    disp = hab["dispersion"]
    models = [[name, f"{v['n']:,}", f"{v['positive_rate']:.3f}",
               f"{v['auc']:.3f} [{v['auc_ci'][0]:.3f}, {v['auc_ci'][1]:.3f}]",
               f"{v['shuffled_mean']:.3f} / {v['shuffled_p95']:.3f}"] for name, v in pr["models"].items()]
    matches = [["Complaint reception channel vs habit, all scored complaints", *_diff(r["complaint_match"]["all"])],
               ["Same, Cargo no reconocido only", *_diff(r["complaint_match"]["disputes"])],
               ["Contact channel vs habit, CSAT", *_diff(sm["contact_channel"])],
               ["Survey send channel vs habit, CSAT", *_diff(sm["send_channel"])]]
    matches += [[f"Survey send channel, within habit {h}", *_diff(d)] for h, d in sm["send_within_habit"].items()]
    out = ["## 1. How satisfaction is measured", "", "Complaint resolution_satisfaction by status:", "",
           _frame(m["coverage"]), "", "resolution_satisfaction (1-5), complaints and share of scored complaints (%):",
           "", _frame(m["scores"]), "",
           "Surveys by type (they link to contact-center interactions, never to a complaint):", "",
           _frame(m["surveys"]), "", "CSAT (1-4) by contact reason:", "", _frame(m["csat_by_reason"]), "",
           "## 2. Dispute satisfaction by case factor", "",
           f"Scored 'Cargo no reconocido' complaints: n={dsp['n']:,}, mean resolution_satisfaction "
           f"{dsp['mean']:.3f}.", ""]
    for factor, frame in dsp["by"].items():
        out += [f"By {factor}:", "", _frame(frame), ""]
    out += ["## 3. Channel habit", "",
            "Channels grouped as voice (Phone), messaging (WhatsApp, Web Chat) and digital (App, Web, Email). "
            "Preference over the full history: fewer than 3 contacts is low-contact; a phone share of 50% or more "
            "is voice-first; otherwise the larger of messaging and digital.", "",
            _frame(hab["contacts_by_group"]), "", f"Customers by preference (of {hab['customers']:,}):", "",
            _frame(hab["preferences"]), "",
            f"Phone-share dispersion, customers with 5 or more contacts (n={disp['customers']:,}): pooled phone "
            f"share {disp['pooled_phone_share']}, observed variance {disp['observed_variance']}, variance expected "
            f"if the channel were random (binomial) {disp['binomial_variance']}, ratio {disp['ratio']}.", "",
            "Next contact by previous contact group:", "", _frame(hab["transitions"]), "",
            f"Mean score when the channel matches the habit minus when it does not. The habit uses only contacts "
            f"before the event (at least {sm['min_prior']}); CSAT surveys n={sm['n']:,}, {sm['with_habit']:,} "
            "with a habit:",
            "", table(["Test", "Difference [95% CI]", "n matched vs not"], matches), "",
            "## 4. Is low satisfaction predictable?", "",
            "Logistic regression, 70/30 split by customer (no customer in both halves), compared with 100 refits on "
            "shuffled training labels.", "",
            table(["Model", "n", "Positive rate", "Test AUC [95% CI]", "Shuffled mean / 95th pct"], models), "",
            "Negative sentiment after a negative contact:", "", _frame(pr["sentiment_persistence"]), "",
            "Share with CSAT <= 2 by prior CSAT mean:", "", _frame(pr["low_by_prior_csat"]), "",
            "Share with CSAT <= 2 by detected sentiment of the same contact:", "", _frame(pr["low_by_sentiment"]), "",
            "Share with CSAT <= 2 by first-contact resolution:", "", _frame(pr["low_by_resolution"]), ""]
    return out


def render_drivers(r: dict, warehouse: str) -> str:
    head = [
        "# What satisfaction depends on", "",
        f"Generated by `python -m data_analytics.satisfaction` from `{warehouse}`, opened read-only. Code: "
        "`data_analytics/satisfaction_drivers.py`. Intervals are 95% (mean +- 1.96 SE for means, bootstrap for "
        "AUC). The organizer dataset is synthetic and templated: a null result here means this data cannot answer "
        "the question, and says nothing about real customers.", "",
        "The question, from a judge: can the service tell a calm customer from an anxious one and tailor the "
        "follow-up (a receipt for one, a call for the other)?", "",
    ]
    return "\n".join(head + _summary(r) + _details(r))


def _is_fixture(con: duckdb.DuckDBPyConnection) -> bool:
    source = con.execute("SELECT source FROM control.runs WHERE status = 'succeeded' LIMIT 1").fetchone()
    return bool(source and "fixture" in source[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What satisfaction depends on")
    parser.add_argument("--warehouse", default=str(REAL_WAREHOUSE), help="warehouse with gold built")
    parser.add_argument("--out", default=str(REPORT_PATH), help="markdown report to write")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    warehouse, out = Path(args.warehouse), Path(args.out)
    if not warehouse.exists():
        print(f"satisfaction failed: {warehouse} does not exist; run the pipeline and `make gold` first",
              file=sys.stderr)
        return 1
    from data_analytics.satisfaction_drivers import analyse  # pandas and scikit-learn only for this report

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        if _is_fixture(con) and out.resolve() == REPORT_PATH:
            print("satisfaction refused: the warehouse holds the synthetic fixture; pass --out to write elsewhere",
                  file=sys.stderr)
            return 1
        result = analyse(con)
    except duckdb.Error as exc:
        print(f"satisfaction failed: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_drivers(result, warehouse.name) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

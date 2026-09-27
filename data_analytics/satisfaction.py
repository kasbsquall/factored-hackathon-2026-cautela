"""Customer satisfaction: what the supplied surveys can and cannot say about the workflow.

Two sources. `complaints.resolution_satisfaction` scores a complaint's resolution (gold.complaint_facts). The
`satisfaction_surveys` table links to contact-center interactions through interaction_id, never to a complaint, so
survey CSAT can be read by contact reason and by first-contact resolution, not by dispute.
"""

from __future__ import annotations

import duckdb

from data_analytics.figures import one, rows

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

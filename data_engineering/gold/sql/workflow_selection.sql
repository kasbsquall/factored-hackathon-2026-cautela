-- One row per complaint type with the figures that decide which workflow to automate, ranked by volume.
-- Every rate sits next to its numerator and denominator so no figure is quoted without them.
WITH totals AS (
    SELECT complaints AS all_complaints
    FROM gold.complaint_outcomes
    WHERE complaint_type = '(all complaints)' AND dimension = 'overall'
)
SELECT o.complaint_type,
       o.is_unrecognized_charge,
       CAST(rank() OVER (ORDER BY o.complaints DESC, o.complaint_type) AS INTEGER) AS volume_rank,
       o.complaints,
       t.all_complaints,
       o.complaints / t.all_complaints AS share_of_complaints,
       o.sla_breached,
       o.sla_breach_rate,
       o.escalated,
       o.escalation_rate,
       o.with_resolution_days,
       o.resolution_days_p50,
       o.resolution_days_p90,
       o.next_complaint_30d,
       o.repeat_30d_rate,
       o._source_table,
       o._source_key,
       o._source_run_ids,
       o._gold_run_id
FROM gold.complaint_outcomes o
CROSS JOIN totals t
WHERE o.dimension = 'overall' AND o.complaint_type <> '(all complaints)'

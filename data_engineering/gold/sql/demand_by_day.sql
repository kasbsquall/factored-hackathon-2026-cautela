-- Daily contact volume per source system, workflow, country and channel: the calendar series behind peak-day and
-- day-of-week sizing.
WITH events AS (
    SELECT 'complaints' AS source_system, complaint_type AS workflow, is_unrecognized_charge,
           customer_country AS country, reception_channel AS channel, event_date,
           _source_run_ids AS run_ids
    FROM gold.complaint_facts
    UNION ALL
    SELECT 'interactions', i.contact_reason, i.contact_reason = 'Cargo no reconocido',
           coalesce(cu.country, 'Unknown'), i.channel, CAST(i.interaction_date AS DATE),
           CASE WHEN cu._run_id IS NULL THEN [i._run_id] ELSE [i._run_id, cu._run_id] END
    FROM silver.call_center_interactions i
    LEFT JOIN silver.customers cu USING (customer_id)
)
SELECT source_system, workflow, is_unrecognized_charge, country, channel, event_date,
       count(*) AS contacts,
       concat_ws('|', source_system, workflow, country, channel, event_date) AS _source_key,
       list_sort(list_distinct(flatten(list(DISTINCT run_ids)))) AS _source_run_ids
FROM events
GROUP BY source_system, workflow, is_unrecognized_charge, country, channel, event_date

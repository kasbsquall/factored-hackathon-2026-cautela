-- Contact volume by day of week and hour of day, per source system, workflow, country and channel. Hours are as
-- stored in the data; the dictionary gives no timezone (see data_engineering/README.md, partition folders).
WITH events AS (
    SELECT 'complaints' AS source_system, complaint_type AS workflow, is_unrecognized_charge,
           customer_country AS country, reception_channel AS channel, iso_dow, hour_of_day,
           _source_run_ids AS run_ids
    FROM gold.complaint_facts
    UNION ALL
    SELECT 'interactions', i.contact_reason, i.contact_reason = 'Cargo no reconocido',
           coalesce(cu.country, 'Unknown'), i.channel, CAST(isodow(i.interaction_date) AS INTEGER),
           CAST(hour(i.interaction_date) AS INTEGER),
           CASE WHEN cu._run_id IS NULL THEN [i._run_id] ELSE [i._run_id, cu._run_id] END
    FROM silver.call_center_interactions i
    LEFT JOIN silver.customers cu USING (customer_id)
)
SELECT source_system, workflow, is_unrecognized_charge, country, channel, iso_dow, hour_of_day,
       count(*) AS contacts,
       concat_ws('|', source_system, workflow, country, channel, iso_dow, hour_of_day) AS _source_key,
       list_sort(list_distinct(flatten(list(DISTINCT run_ids)))) AS _source_run_ids
FROM events
GROUP BY source_system, workflow, is_unrecognized_charge, country, channel, iso_dow, hour_of_day

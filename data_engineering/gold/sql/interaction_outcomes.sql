-- Contact-center outcomes per contact reason: first-contact resolution (was_resolved), escalation, follow-up and
-- handling time, overall and by country, segment and channel. '(all reasons)' rows hold the totals.
WITH i AS (
    SELECT i.reason_category,
           i.contact_reason,
           i.channel,
           coalesce(cu.country, 'Unknown') AS customer_country,
           coalesce(cu.segment, 'Unknown') AS customer_segment,
           i.was_resolved,
           i.was_escalated,
           i.requires_followup,
           i.duration_seconds,
           i.wait_time_seconds,
           i._run_id AS interaction_run_id,
           cu._run_id AS customer_run_id
    FROM silver.call_center_interactions i
    LEFT JOIN silver.customers cu USING (customer_id)
),
grouped AS (
    SELECT reason_category,
           contact_reason,
           CASE WHEN grouping(customer_country) = 0 THEN 'customer_country'
                WHEN grouping(customer_segment) = 0 THEN 'customer_segment'
                WHEN grouping(channel) = 0 THEN 'channel'
                ELSE 'overall' END AS dimension,
           coalesce(customer_country, customer_segment, channel, 'all') AS dimension_value,
           count(*) AS interactions,
           count(was_resolved) AS fcr_denominator,
           count(*) FILTER (WHERE was_resolved) AS resolved_first_contact,
           avg(was_resolved::INTEGER) AS fcr_rate,
           count(*) FILTER (WHERE was_escalated) AS escalated,
           avg(was_escalated::INTEGER) AS escalation_rate,
           count(*) FILTER (WHERE requires_followup) AS requires_followup,
           count(duration_seconds) AS with_duration,
           quantile_cont(duration_seconds, 0.5) AS duration_p50_seconds,
           quantile_cont(duration_seconds, 0.9) AS duration_p90_seconds,
           avg(duration_seconds) AS duration_mean_seconds,
           count(wait_time_seconds) AS with_wait,
           quantile_cont(wait_time_seconds, 0.5) AS wait_p50_seconds,
           quantile_cont(wait_time_seconds, 0.9) AS wait_p90_seconds,
           list_sort(list_distinct(list_concat(list(DISTINCT interaction_run_id),
                                               list(DISTINCT customer_run_id)
                                                   FILTER (WHERE customer_run_id IS NOT NULL))))
               AS _source_run_ids
    FROM i
    GROUP BY GROUPING SETS (
        (reason_category, contact_reason),
        (reason_category, contact_reason, customer_country),
        (reason_category, contact_reason, customer_segment),
        (reason_category, contact_reason, channel),
        (),
        (customer_country),
        (customer_segment),
        (channel)
    )
)
SELECT * EXCLUDE (reason_category, contact_reason, _source_run_ids),
       coalesce(reason_category, '(all reasons)') AS reason_category,
       coalesce(contact_reason, '(all reasons)') AS contact_reason,
       coalesce(reason_category, '(all reasons)') || '|' || coalesce(contact_reason, '(all reasons)') || '|'
           || dimension || '=' || dimension_value AS _source_key,
       _source_run_ids
FROM grouped

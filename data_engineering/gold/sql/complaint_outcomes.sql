-- Complaint outcomes per complaint type, overall and broken down by country, segment and reception channel.
-- Rows with complaint_type '(all complaints)' are the totals every share is computed against. Percentiles are
-- computed per group (they are not additive), so each breakdown is its own grouping set.
WITH grouped AS (
    SELECT complaint_type,
           is_unrecognized_charge,
           CASE WHEN grouping(customer_country) = 0 THEN 'customer_country'
                WHEN grouping(customer_segment) = 0 THEN 'customer_segment'
                WHEN grouping(reception_channel) = 0 THEN 'reception_channel'
                ELSE 'overall' END AS dimension,
           coalesce(customer_country, customer_segment, reception_channel, 'all') AS dimension_value,
           count(*) AS complaints,
           count(DISTINCT customer_id) AS distinct_customers,
           count(*) FILTER (WHERE sla_breached) AS sla_breached,
           avg(sla_breached::INTEGER) AS sla_breach_rate,
           count(*) FILTER (WHERE is_escalated) AS escalated,
           avg(is_escalated::INTEGER) AS escalation_rate,
           count(resolution_days) AS with_resolution_days,
           quantile_cont(resolution_days, 0.5) AS resolution_days_p50,
           quantile_cont(resolution_days, 0.9) AS resolution_days_p90,
           count(first_response_hours) AS with_first_response,
           quantile_cont(first_response_hours, 0.5) AS first_response_hours_p50,
           quantile_cont(first_response_hours, 0.9) AS first_response_hours_p90,
           count(*) FILTER (WHERE is_repeat_complainer) AS repeat_flag_delivered,
           count(*) FILTER (WHERE prior_complaint_90d) AS prior_complaint_90d,
           count(*) FILTER (WHERE is_repeat_complainer AND prior_complaint_90d) AS repeat_flag_confirmed,
           count(*) FILTER (WHERE next_complaint_30d) AS next_complaint_30d,
           avg(next_complaint_30d::INTEGER) AS repeat_30d_rate,
           count(claimed_amount) AS with_claimed_amount,
           count(resolution_satisfaction) AS with_satisfaction,
           avg(resolution_satisfaction) AS satisfaction_mean,
           min(event_date) AS first_date,
           max(event_date) AS last_date,
           list_sort(list_distinct(flatten(list(DISTINCT _source_run_ids)))) AS _source_run_ids
    FROM gold.complaint_facts
    GROUP BY GROUPING SETS (
        (complaint_type, is_unrecognized_charge),
        (complaint_type, is_unrecognized_charge, customer_country),
        (complaint_type, is_unrecognized_charge, customer_segment),
        (complaint_type, is_unrecognized_charge, reception_channel),
        (),
        (customer_country),
        (customer_segment),
        (reception_channel)
    )
)
SELECT * EXCLUDE (complaint_type, _source_run_ids),
       coalesce(complaint_type, '(all complaints)') AS complaint_type,
       coalesce(complaint_type, '(all complaints)') || '|' || dimension || '=' || dimension_value AS _source_key,
       _source_run_ids
FROM grouped

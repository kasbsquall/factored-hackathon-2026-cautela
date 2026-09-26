-- One row per silver complaint with the workflow label, the customer's country and segment, timing, and
-- repeat-contact features measured from the complaint history itself.
-- The unrecognized-charge workflow is matched on the literal label in either field: the organizer data puts
-- "Cargo no reconocido" in subcategory (category "Transactions"), the synthetic fixture puts it in category.
WITH ordered AS (
    SELECT c.*,
           lag(c.creation_date) OVER w AS previous_creation,
           lead(c.creation_date) OVER w AS next_creation
    FROM silver.complaints c
    WINDOW w AS (PARTITION BY c.customer_id ORDER BY c.creation_date, c.complaint_id)
)
SELECT o.complaint_id,
       o.customer_id,
       o.creation_date,
       CAST(o.creation_date AS DATE) AS event_date,
       CAST(isodow(o.creation_date) AS INTEGER) AS iso_dow,
       CAST(hour(o.creation_date) AS INTEGER) AS hour_of_day,
       o.category,
       o.subcategory,
       o.category || ' / ' || coalesce(o.subcategory, '(no subcategory)') AS complaint_type,
       (o.category = 'Cargo no reconocido' OR coalesce(o.subcategory, '') = 'Cargo no reconocido')
           AS is_unrecognized_charge,
       o.case_type,
       o.reception_channel,
       o.priority,
       o.status,
       (o.status = 'Escalated') AS is_escalated,
       coalesce(cu.country, 'Unknown') AS customer_country,
       coalesce(cu.segment, 'Unknown') AS customer_segment,
       o.claimed_amount,
       o.currency,
       o.sla_breached,
       o.resolution_days,
       date_diff('second', o.creation_date, o.first_response_date) / 3600.0 AS first_response_hours,
       o.resolution_satisfaction,
       o.compensation_granted,
       o.is_repeat_complainer,
       coalesce(o.previous_creation >= o.creation_date - INTERVAL 90 DAY, false) AS prior_complaint_90d,
       coalesce(o.next_creation <= o.creation_date + INTERVAL 30 DAY, false) AS next_complaint_30d,
       o.complaint_id AS _source_key,
       list_sort(list_distinct(list_concat([o._run_id],
                                           CASE WHEN cu._run_id IS NULL THEN []::VARCHAR[] ELSE [cu._run_id] END)))
           AS _source_run_ids
FROM ordered o
LEFT JOIN silver.customers cu USING (customer_id)

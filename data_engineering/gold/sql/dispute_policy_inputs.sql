-- The facts agent/policy needs to decide eligibility for one transaction: who (country, segment), what product
-- and in what state, which channel, when, and how much. No policy is computed here; the rules stay in code.
SELECT t.transaction_id,
       t.customer_id,
       c.country AS customer_country,
       c.segment AS customer_segment,
       c.customer_status,
       t.product_id,
       t.product_type,
       t.product_status,
       t.channel,
       t.transaction_type,
       t.transaction_status,
       t.transaction_date,
       t.amount,
       t.currency,
       t.amount_usd,
       t.fraud_score,
       t.is_fraud,
       t._source_table,
       t._source_key,
       list_sort(list_distinct(list_concat(t._source_run_ids, coalesce(c._source_run_ids, []::VARCHAR[]))))
           AS _source_run_ids,
       t._gold_run_id
FROM gold.customer_transactions t
LEFT JOIN gold.customer_profile c USING (customer_id)

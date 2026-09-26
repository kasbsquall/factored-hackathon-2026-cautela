-- One row per silver transaction with the product it moved, for list_recent_transactions and get_transaction.
-- "Recent" depends on the request time, so the window is applied when the tool queries this table.
SELECT t.transaction_id,
       t.customer_id,
       t.product_id,
       p.product_type,
       p.product_status,
       t.transaction_date,
       t.transaction_type,
       t.transaction_status,
       t.transaction_category,
       t.amount,
       t.currency,
       t.amount_usd,
       t.channel,
       t.merchant_name,
       t.merchant_category,
       t.transaction_country,
       t.transaction_city,
       t.branch_id,
       t.fraud_score,
       t.is_fraud,
       t.transaction_id AS _source_key,
       list_sort(list_distinct(list_concat([t._run_id],
                                           CASE WHEN p._run_id IS NULL THEN []::VARCHAR[] ELSE [p._run_id] END)))
           AS _source_run_ids
FROM silver.transactions t
LEFT JOIN silver.products p USING (product_id)

-- One row per silver product for the tools' product reads and the permission layer's ownership check.
SELECT p.product_id,
       p.customer_id,
       p.product_type,
       p.product_number,
       p.currency,
       p.product_status,
       p.product_id AS _source_key,
       [p._run_id] AS _source_run_ids
FROM silver.products p

-- One row per silver customer: identity-free profile for get_customer_profile. Contact details (email, phones,
-- address, document number) are left out on purpose; the identity service reads them from silver.
WITH products AS (
    SELECT customer_id,
           count(*) AS products_total,
           count(*) FILTER (WHERE product_status = 'Active') AS products_active,
           count(*) FILTER (WHERE product_type IN ('Credit Card', 'Debit Card')) AS cards_total,
           list_sort(list_distinct(list(currency))) AS product_currencies,
           list_distinct(list(_run_id)) AS run_ids
    FROM silver.products
    GROUP BY customer_id
)
SELECT c.customer_id,
       c.first_name,
       c.last_name,
       c.country,
       c.state,
       c.city,
       c.segment,
       c.customer_status,
       c.registration_date,
       coalesce(p.products_total, 0) AS products_total,
       coalesce(p.products_active, 0) AS products_active,
       coalesce(p.cards_total, 0) AS cards_total,
       coalesce(p.product_currencies, []::VARCHAR[]) AS product_currencies,
       (b.branch_id IS NOT NULL) AS registration_branch_known,
       c.customer_id AS _source_key,
       list_sort(list_distinct(list_concat([c._run_id], coalesce(p.run_ids, []::VARCHAR[]),
                                           CASE WHEN b._run_id IS NULL THEN []::VARCHAR[] ELSE [b._run_id] END)))
           AS _source_run_ids
FROM silver.customers c
LEFT JOIN products p USING (customer_id)
LEFT JOIN silver.branches b ON b.branch_id = c.registration_branch_id

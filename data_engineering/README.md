# Data engineering

Bronze and silver layers for the LATAM Bank dataset, built against the organizer data dictionary and a labeled synthetic test fixture. The organizer data has not arrived yet (it may come as files or through an S3 connection), so everything here is source-agnostic and tested without it.

## Run it

```bash
uv sync
make fixture    # uv run python -m data_engineering.fixtures.generate --out data/fixture --seed 42
make pipeline   # uv run python -m data_engineering.pipelines.run --source data/fixture --target data/warehouse.duckdb
make test       # uv run pytest
```

On Windows without `make`, run the commands in the comments. `--tables transactions complaints` limits a run to a subset (parents must already be loaded) and `--full-refresh` rebuilds the selected tables. Everything under `data/` is git-ignored.

The real dataset is on Amazon S3 with read-only participant credentials. Copy `.env.example` to `.env` (git-ignored) and fill `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` (us-east-2 when empty) and `LATAM_BANK_S3_URI`. Then:

```bash
make pipeline-s3   # uv run python -m data_engineering.pipelines.run --target data/warehouse_s3.duckdb
```

Without `--source`, the pipeline reads `LATAM_BANK_S3_URI`. The real data gets its own warehouse file on purpose: a warehouse refuses to load a second source, because the file ledger keys files by path relative to the source root and a fixture file could otherwise shadow a real file with the same relative path.

## Layout

| Path | What it holds |
|---|---|
| `contracts/<table>.yaml` | One contract per table, 13 in total |
| `contracts/loader.py` | Pydantic models that validate the YAML and cross-check foreign keys |
| `fixtures/` | Deterministic synthetic fixture generator and its `manifest.json` |
| `pipelines/source.py` | Local or S3 file discovery, per-file schemas, S3 credentials from env |
| `pipelines/env.py` | Minimal `.env` loader for the S3 settings |
| `pipelines/bronze.py` | Raw landing and schema drift detection |
| `pipelines/checks.py` | Typing, contract checks, quarantine |
| `pipelines/silver.py` | Deduplication, upsert, batch metrics |
| `pipelines/warehouse.py` | Warehouse schemas, file ledger, watermarks |
| `pipelines/report.py` | Quality report JSON |
| `pipelines/run.py` | CLI entry point |
| `pipelines/gold.py` | Placeholder, see below |

## Design and the reasons behind it

### Engine: DuckDB over Parquet

The whole pipeline reproduces on a laptop with one command and costs nothing to run. The SQL is plain enough (window functions, `QUALIFY`, `MERGE`-style delete and insert) to port to Databricks or Snowflake if the service ever needs a cluster. The trade-off is a single node, covered under capacity limits below.

### Contracts come from the dictionary, with provenance

Each column records its type, nullability, primary key, uniqueness, foreign key, value list and range exactly as the dictionary states them. The PDF-to-text extraction garbled several tables, so every column also says how its facts were obtained:

| `provenance` | Meaning |
|---|---|
| `dictionary` | Read from an aligned row |
| `realigned` | The extraction printed descriptions and constraints below the column list; they were matched by position after checking that the counts agree (for example 22 columns and 22 descriptions in `transactions`) |
| `decoded` | The value list was interleaved with the constraint text (`AdNjuOsTtmNeUntL)L` is "Adjustment" plus "NOT NULL") and was decoded by removing the known constraint letters |

A value list that is truncated is marked `enum_status: unknown`, carries a note, and is profiled in the quality report instead of being guessed. Nothing was invented to fill a gap.

### Severity: what quarantines and what only reports

Every column has a `severity`, and `check_severity` can override it for one check.

- Required columns (`NOT NULL`) and stated numeric ranges are enforced (`error`).
- Optional columns, nullable foreign keys and optional value lists are reported (`warn`).
- Decoded value lists only warn until the real data confirms them. A wrong decoding should show up as a warning count in the report, while the rows stay usable.
- A null primary key and a value that cannot be cast to the contract type always quarantine. The pipeline cannot key such a row, and storing a null in place of an unreadable value would fabricate a missing value.
- Unique columns (`document_number`, `product_number`, `branch_code`, `employee_code`) are reported only, because there is no principled way to decide which of two rows is the wrong one.

### Bronze keeps everything as text

Bronze stores every column as VARCHAR with `_source_file`, `_ingested_at` and `_run_id`. Files of the same table can disagree on types (the fixture writes `amount` as a number in 59 partitions and as text in one), and a text landing zone accepts both without losing the original value. Files are read in groups of identical schema, so DuckDB never has to reconcile conflicting types inside one scan. New columns are added to bronze as they appear.

### Quarantine instead of silent drops

Silver casts each column with `TRY_CAST` and evaluates every check per row. A row that fails any error-severity check goes to `quarantine.records` with its reason codes, the failing columns and the raw record as received. Nothing is dropped without a trace, so the counts can be explained to anyone who asks where a row went.

| Reason code | Meaning |
|---|---|
| `null_pk` | A primary key column is null |
| `type_cast_error` | A non-empty value does not fit the contract type (for example `1.234,56` in a DECIMAL column) |
| `null_required` | A NOT NULL column is null |
| `bad_enum` | Value outside the dictionary's list |
| `out_of_range` | Value outside the dictionary's stated range |
| `orphan_fk` | The key was never delivered in the parent table |

An orphan is checked against every key the parent ever delivered, whether it landed in silver or in quarantine. A transaction whose customer row was quarantined for a bad segment still references a real customer, so it stays in silver, and the customer problem stays visible in quarantine.

Warn-level findings travel with the row in `_quality_warnings`, so a downstream consumer can tell that, say, a complaint status was outside the decoded list.

### Deduplication and upsert

Silver keeps one row per primary key, the latest by the contract's `dedupe_order`: `process_date` for daily facts (a corrected version that lands in a later partition wins) and `last_updated` for customers and products. Branches, agents and campaigns have no update timestamp, so the latest snapshot file wins. Ties fall back to the source file path and then a content hash, so the result never depends on read order.

The content hash excludes the partition column. A record re-delivered unchanged in another partition counts as an exact duplicate; a record with changed content counts as a new version. The report separates the two, and `ambiguous_versions` counts keys with different content under the same ordering value, which would mean the tie-break decided something the data should have.

The upsert only touches keys present in the batch: it deletes them from silver and inserts the winners of the existing and new rows together.

### Incremental loads, idempotency and late arrivals

`control.file_ledger` records every file already loaded, and `control.watermarks` stores the high-water mark (max partition value) per table. A run reads only files that are not in the ledger. Two properties follow:

- A rerun on the same source reads nothing and changes nothing.
- A file that lands late inside an old partition is still loaded. A pure date watermark would skip it; the report counts such rows as `late_partition_rows`.

Each table is processed in one transaction: bronze insert, quarantine, silver upsert, ledger and watermark commit together or not at all. A crash leaves the ledger untouched, and the next run reloads the files.

Late arrival is measured as `process_date - event date > 1 day` (`event_lag_rows`). The one-day threshold is our assumption; the dictionary only says partitions "may arrive late".

The problem statement allows proving update correctness with a labeled fixture because the data is static. `tests/test_incremental.py` delivers the fixture in two waves, withholding one early partition from the first, and requires the final silver to equal a single full load row for row.

### Schema drift

Before reading, the pipeline compares each new file's columns and physical types with the contract and reports `unexpected_column`, `missing_column` and `type_changed` events. Drift never stops the run. Unexpected columns are kept in bronze and stay out of silver until someone adds them to the contract. A missing required column makes its rows fail `null_required`, which is visible in the report.

### Lineage

Every bronze and silver row keeps `_source_file` (relative to the source root), `_ingested_at` and `_run_id`. Quarantined rows keep the same plus the raw record. `control.runs` ties each run to its source and report.

### Quality report

Each run writes `data/reports/quality_<run_id>.json` with, per table: files discovered and new; rows in, quarantined, valid, exact duplicates, superseded versions, inserted, updated and total in silver; duplicate rate; null rate per column; orphan count and rate per foreign key; quarantine and warning counts by reason and by column; late arrivals; unique violations; drift events; value profiles for unknown or decoded lists; and the watermark before and after. When the source is the fixture, the report carries its label.

### Source-agnostic reader

A source is a local directory or an `s3://bucket/prefix`. Both go through DuckDB, so no other code branches on location. Per table the reader accepts `<root>/<table>/**/*.parquet|csv` or `<root>/<table>.parquet|csv`, because the delivery layout is still unconfirmed. S3 credentials come only from environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` defaulting to us-east-2, plus the optional `AWS_SESSION_TOKEN` and `AWS_ENDPOINT_URL`), which the CLI loads from `.env` with a small built-in loader that never overrides variables already set. Without keys the AWS credential chain is used. Credentials embedded in the URI are rejected, and the report records only a redacted description of how authentication happened. The tests cover URI parsing, secret construction and the `.env` loader without touching the network.

### The synthetic test fixture

`fixtures/generate.py` writes a small, deterministic dataset (seed 42 by default, about 77,000 rows, a few seconds) shaped by the contracts: roughly the organizer row counts divided by 250, Spanish names and cities for Mexico, Colombia and Argentina, 60 daily partitions. It is labeled **SYNTHETIC TEST FIXTURE, team-generated, not organizer data** in `manifest.json` and in every quality report built from it. Category and reason labels are plausible Spanish wording chosen by the team; the organizer vocabulary may differ.

It injects the issues the dictionary announces, each on a distinct row so the counts are exact: about 2% exact duplicates, about 5% nulls in nullable columns, orphan keys, invalid values, null keys, out-of-range values, text amounts with a decimal comma, late rows, re-delivered versions of pending transactions and open complaints, and a new `installments` column in the last 15 transaction partitions. Dispute complaints are tied to a real fixture transaction (same customer, product, amount and currency, created afterwards), and the manifest records these links as ground truth.

### Gold is a placeholder

`pipelines/gold.py` is intentionally empty. Gold tables serve the agent tools and the analytics and ML datasets, and their shape depends on the workflow decision that waits on the exploratory analysis of `contact_reason` and `complaints.category`.

## Result on the fixture (seed 42)

Synthetic test fixture, team-generated, not organizer data. Full run: 484 files and 77,124 rows in, 62 rows quarantined, 1,511 exact duplicates removed, 70 superseded versions, 75,481 rows in silver, 2 drift events. Every count matches the manifest.

| Reason | Quarantined rows |
|---|---|
| orphan_fk | 19 |
| bad_enum | 13 |
| out_of_range | 12 |
| null_pk | 9 |
| type_cast_error | 6 |
| null_required | 3 |

Warnings (rows kept): 14 orphan keys on nullable foreign keys and 6 values outside decoded lists. Late arrivals: 140 in transactions, 100 in digital events, 40 in complaints, 20 in call center interactions.

## What the dictionary leaves unclear

These are recorded in the contracts as notes. They need confirmation from the organizers or from the real data.

1. `products`, `service_agents`, `transactions`, `satisfaction_surveys`, `digital_events` and `complaints`: descriptions and constraints are detached from their columns in the extraction. They were realigned by position, and the counts agree in every case.
2. `products.product_type`: the value list is truncated after "Investme". Decoded prefix: Checking Account, Savings Account, Credit Card, Debit Card, Personal Loan, Mortgage. No enum check is applied.
3. Decoded last values, to be confirmed: `transactions.transaction_type` (Adjustment), `transactions.transaction_category` (Other, and nullable), `call_center_interactions.reason_category` (Complaint), `digital_events.event_type` (Purchase), `complaints.status` (Rejected), `marketing_campaigns.campaign_objective` (Reactivation), `customers.detected_accent` ("ne-utral" read as neutral, nullable).
4. `service_agents.experience_level` lists both "Mid-Senior" and "Senior", which may be an extraction artifact.
5. `satisfaction_surveys.main_score`: 1-5 for CSAT and 0-10 for NPS; no range for CES. The contract checks 0-10 and only warns.
6. `satisfaction_surveys.campaign_response_rate`: unclear meaning in a survey table.
7. `satisfaction_surveys.interaction_id` and `agent_id` come out nullable after realignment, while `customer_id` is required.
8. `complaints` has no `transaction_id`. Linking a dispute to the charge has to be inferred from customer, product, amount and dates.
9. `complaints.category`, `subcategory`, `call_center_interactions.contact_reason` and `comment_sentiment` have no value lists. They are profiled, since the workflow choice depends on them.
10. The SLA threshold behind `complaints.sla_breached` is not documented.
11. `transactions.currency`, `complaints.currency` and `branches.country` have no value lists, although `products.currency` and `customers.country` do.
12. `call_transcripts` has no event timestamp, so late arrival cannot be measured from that table alone. `transcription_model` is an open list ("Whisper, Google STT, etc.").
13. `daily_exchange_rates` is partitioned daily but has no `process_date`; the rate date is used. The direction of `exchange_rate` is not documented, and 3,000 rows over three years does not match three currencies per calendar day (about 3,300), so the pairs or the day coverage may differ from what we assume.
14. `branches`, `service_agents` and `marketing_campaigns` have no update timestamp, so deduplication relies on snapshot file order.
15. Snapshot tables (`monthly_snapshot`, `full_snapshot`) have no snapshot date column, and the number and layout of snapshots is unknown.
16. The row-count column in `dataset-summary.txt` is shifted by one row (it prints 350 next to `service_agents`). The contracts use the per-table counts from the dictionary.

## Capacity limits and route to production

DuckDB runs on one machine. The organizer dataset (about 19 million rows, the largest table at 10 million) fits comfortably; the fixture run takes a few seconds. Past what one machine's disk and memory hold, the same SQL can run on Databricks or Snowflake with the ledger and watermark tables kept as they are. Other known limits: files are assumed immutable once landed (a file rewritten under the same name is not reloaded); rows quarantined as orphans are not replayed automatically when their parent arrives later; deletions in snapshot tables are not propagated; and VARCHAR lengths are not enforced.

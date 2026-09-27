# Data engineering

Bronze, silver and gold layers for the LATAM Bank dataset, built against the organizer data dictionary, tested on a labeled synthetic test fixture and run on the organizer data (CSV files in an S3 bucket, under `data/`). The reader is source-agnostic: a local directory and an `s3://` prefix go through the same code. The committed pipeline numbers on organizer data were produced from a local copy of the bucket in `data/raw`, which `make mirror` fills.

## Run it on the synthetic fixture

```bash
uv sync
make fixture    # uv run python -m data_engineering.fixtures.generate --out data/fixture --seed 42
make pipeline   # uv run python -m data_engineering.pipelines.run --source data/fixture --target data/warehouse.duckdb
make gold       # uv run python -m data_engineering.gold.run --target data/warehouse.duckdb
make test       # uv run pytest
```

`make fixture pipeline gold` took 15 s on the laptop that built this. On Windows without `make`, run the command under each target in the `Makefile`. `--tables transactions complaints` limits a run to a subset (parents must already be loaded) and `--full-refresh` rebuilds the selected tables. Everything under `data/` is git-ignored.

## Reproduce from zero on the organizer data

The organizer data is on Amazon S3 with read-only participant credentials. The organizer warehouse is always `data/warehouse_real.duckdb` (`WAREHOUSE_REAL` in the `Makefile`), and the `Makefile` targets that read organizer data (`gold-real`, `analytics`, `demo-seed`, `demo-artifacts`) default to it, as do the eval scripts. Times below were measured once each on one Windows laptop, so read them as orders of magnitude. Disk: about 5.35 GB for `data/raw` and 4.2 GB for the warehouse.

| Step | Command | Measured |
|---|---|---|
| 0. Dependencies | `uv sync` | |
| 1. Credentials | copy `.env.example` to `.env` (git-ignored) and fill `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` (us-east-2 when empty) and `LATAM_BANK_S3_URI=s3://<bucket>/data` | |
| 2. Local copy of the bucket | `make mirror` | 7,671 files, 5.35 GB. With the same copy code: the 11 smaller tables (5,491 files, 1.27 GB) in 265 s during `make freshness-demo`, and digital_events plus campaign_sends (2,180 files, 4.08 GB) in 112 s on 2026-09-27. Memory not measured |
| 3a. Bronze and silver, 11 tables | `make pipeline-real TABLES=branches,customers,service_agents,call_center_interactions,call_transcripts,marketing_campaigns,products,complaints,daily_exchange_rates,satisfaction_surveys,transactions` | 10.0 minutes, 6,127,393 rows. Memory not measured |
| 3b. Bronze and silver, the two large tables | `make pipeline-real` (the 11 tables are already in the ledger, so only digital_events and campaign_sends are read) | 36.3 minutes, 17,367,795 rows; the process held at least 23 GB of memory and spilled at least 19 GB to its temp directory (observed readings, not measured peaks) |
| 4. Gold | `make gold-real` | first build 32 s, rerun 3 s. Memory not measured |
| 5. Analytics report | `make analytics` | not timed |
| 6. Freshness | `make freshness-status TARGET=data/warehouse_real.duckdb` | seconds; exits 1 on this static delivery, because every daily table is months behind the calendar |

Steps 3a and 3b are the two runs behind the committed numbers. A single `make pipeline-real` loads all 13 tables in one run; that run was never timed. The expected result is `reports/organizer_load.md`: 13 tables, 23,495,188 rows, 0 quarantined. `make mirror` keeps each object's S3 `LastModified` as the file time, so the file ledger fingerprints what the bucket holds, and a rerun copies only files whose size or time changed.

`LATAM_BANK_S3_URI` must name the prefix that holds the table folders (`s3://<bucket>/data`); pointed at the bucket root, `make mirror` stops and names the tables it did not find. The pipeline can also read the bucket directly (`uv run python -m data_engineering.pipelines.run --target <another file>` reads `LATAM_BANK_S3_URI` when `--source` is omitted). That route uses the same reader but was never run at full volume, and it needs its own warehouse file: a warehouse refuses to load a second source, because the file ledger keys files by path relative to the source root and a file from one source could otherwise shadow a file with the same relative path from another. `make pipeline-s3` is kept as another name for `make pipeline-real`.

### Which committed numbers cover which tables

| Number | Where | Tables |
|---|---|---|
| 0 of 6,127,393 rows quarantined after reconciliation, 10 minutes | the reconciliation section below | the 11 tables other than digital_events and campaign_sends, which were not in the local copy yet |
| 13 tables, 23,495,188 rows, 0 quarantined | `reports/organizer_load.md` | all 13 |
| 31 of 31 objects equal to a single load | `reports/freshness_backup_vs_current.md` | the same 11 tables; the two large ones are compared by file listing only |
| gold row counts, first build 32 s | the gold section below | no gold table reads digital_events or campaign_sends |
| every figure in `data_analytics/reports/` | `why-this-workflow.md` | no figure reads digital_events or campaign_sends |

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
| `pipelines/mirror.py` | `make mirror`: copies the bucket into `data/raw`, keeping each object's modification time |
| `pipelines/load_summary.py` | Aggregate-only summary of quality reports, committed as `reports/organizer_load.md` |
| `pipelines/gold.py` | Old placeholder, superseded by `gold/` |
| `gold/` | Gold layer: contracts, SQL, incremental build, checks and CLI (see the gold section) |
| `freshness/` | Freshness status against the policy, and the update test on organizer data (stage, compare, demo, report); see the freshness section |
| `reports/` | Committed reports built from organizer data (aggregates only) |
| `slice.py` | Copies chosen customers from a built warehouse into a small one with every lineage column, for the public demo bundle (see below) |

## Demo slice

`slice.py` writes a small warehouse with only the customers the public demo uses (`deploy/demo_select.py` picks them,
`deploy/bundle.py` calls the slice). Rows are copied, never rebuilt: bronze, silver and the gold serving tables
(`customer_profile`, `customer_transactions` and, since it was added, `customer_products`) keep `_source_file`, `_ingested_at`, `_run_id`, `_source_table`,
`_source_key`, `_source_run_ids` and `_gold_run_id` as they were. Tables are created from the source DDL, the
`dispute_policy_inputs` view from its own definition, and `control.runs` and `control.gold_runs` are copied whole, so
the tool repository's freshness check sees the same history. `control.file_ledger` keeps only the files the copied
bronze rows came from, and `control.demo_slice` records which scenario each customer was chosen for and from which
warehouse. The gold analytics tables are left out: they aggregate every customer and would be wrong for a slice.

For the eight demo customers of the organizer data the slice is 284 transactions and 3.9 MB (the full warehouse was
1.8 GB when the slice was cut; with all 13 tables it is 4.2 GB). The committed bundle lock predates
`customer_products`, so the shipped slice does not hold it; the next `make demo-artifacts` needs `make gold-real` first
and writes a new lock. Two slices of the same customers hold the same rows but not the same file bytes (DuckDB's block
layout), so `content_digest()` hashes the rows in a fixed order; the bundle lock records both. Tests: `tests/deploy/test_slice.py`.

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

Each column also carries a data `classification` (see the data classification section). A value list that is truncated is marked `enum_status: unknown`, carries a note, and is profiled in the quality report instead of being guessed. Nothing was invented to fill a gap.

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
| `parent_quarantined` | The parent key was delivered but that parent row is in quarantine (always a warning) |
| `partition_path_mismatch` | The partition column disagrees with the file's `year=/month=/day=` folders (warning) |

An orphan is checked against every key the parent delivered to bronze. A child whose parent row was quarantined for a non-key reason (a bad segment, say) references a real parent, so it stays in silver with a `parent_quarantined` warning, and the report counts orphans and parent-quarantined children separately for every foreign key. A parent's quality problem therefore never cascades into its children.

### Normalization before the checks

Some delivered values are an accented or translated form of a documented value ("México" for "Mexico", "Urbana" for "Urban"). A column can declare an explicit `normalize` map, applied in silver before any check; the loader rejects a map whose targets fall outside the column's value list, so a map cannot widen a list silently. Values the dictionary does not list at all are accepted only through `observed_values`, which requires a note. The raw value stays in bronze, and each silver row lists what was normalized in `_normalized` (for example `country:México`).

### Encoding

Before reading, every CSV file's bytes are checked: strict UTF-8 (with or without BOM) is read as UTF-8, anything else is read as Latin-1 and listed in the report. After loading, any U+FFFD replacement character in the batch stops the table with an error and nothing is committed, because it means text was decoded with the wrong encoding upstream. On the organizer data all 5,491 files are UTF-8 with BOM and no replacement character exists. The "T�cnico" seen in the first run's console output came from printing through a Windows console code page; the stored value is "Técnico" (U+00E9). The CLI now writes UTF-8 to stdout.

### Partition folders

Daily tables arrive under `year=/month=/day=` folders. DuckDB's automatic Hive detection is switched off, so those folders do not become columns that look like drift. The folder date is instead validated against the partition column and reported as `partition_path_mismatch` (0 mismatches on the organizer data). A file that also carries `year`, `month` or `day` as data columns keeps them in bronze without a drift event.

Warn-level findings travel with the row in `_quality_warnings`, so a downstream consumer can tell that, say, a complaint status was outside the decoded list.

### Deduplication and upsert

Silver keeps one row per primary key, the latest by the contract's `dedupe_order`: `process_date` for daily facts (a corrected version that lands in a later partition wins) and `last_updated` for customers and products. Branches, agents and campaigns have no update timestamp, so the latest snapshot file wins. Ties fall back to the source file path and then a content hash, so the result never depends on read order.

The content hash excludes the partition column. A record re-delivered unchanged in another partition counts as an exact duplicate; a record with changed content counts as a new version. The report separates the two, and `ambiguous_versions` counts keys with different content under the same ordering value, which would mean the tie-break decided something the data should have.

The upsert only touches keys present in the batch: it deletes them from silver and inserts the winners of the existing and new rows together.

### Incremental loads, idempotency and late arrivals

`control.file_ledger` records every file version loaded, with its size and modification time (S3 `LastModified` for `s3://` sources), and `control.watermarks` stores the high-water mark (max partition value) per table. A run reads files that are not in the ledger and files whose size or modification time changed. Three properties follow:

- A rerun on the same source reads nothing and changes nothing.
- A file that lands late inside an old partition is still loaded. A pure date watermark would skip it; the report counts such rows as `late_partition_rows`.
- A file rewritten under the same name is loaded again, and its old version is withdrawn (see the freshness section).

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

### Gold

The gold layer is built from silver by `gold/`; see the gold layer section below.

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

Warnings (rows kept): 832 children of quarantined parents, 14 orphan keys on nullable foreign keys and 6 values outside decoded lists. Late arrivals: 140 in transactions, 100 in digital events, 40 in complaints, 20 in call center interactions.

## What the dictionary leaves unclear

These are recorded in the contracts as notes. They need confirmation from the organizers or from the real data.

1. `products`, `service_agents`, `transactions`, `satisfaction_surveys`, `digital_events` and `complaints`: descriptions and constraints are detached from their columns in the extraction. They were realigned by position, and the counts agree in every case.
2. `products.product_type`: the value list is truncated after "Investme". Decoded prefix: Checking Account, Savings Account, Credit Card, Debit Card, Personal Loan, Mortgage. Resolved with the delivered data, see the reconciliation below.
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
13. `daily_exchange_rates` is partitioned daily but has no `process_date`; the rate date is used. The delivered data answers the other two questions: rates are given for every directed pair (source to target), and there are 13,164 rows, see the reconciliation below.
14. `branches`, `service_agents` and `marketing_campaigns` have no update timestamp, so deduplication relies on snapshot file order.
15. Snapshot tables (`monthly_snapshot`, `full_snapshot`) have no snapshot date column, and the number and layout of snapshots is unknown.
16. The row-count column in `dataset-summary.txt` is shifted by one row (it prints 350 next to `service_agents`). The contracts use the per-table counts from the dictionary.

## Reconciliation with the delivered data

The first run over the organizer data (local mirror of the S3 bucket, 5,491 CSV files, 6,127,393 rows) showed where the dictionary and the data disagree. The contracts now follow the data, and every difference is recorded here and in the column's `note`. Counts come from the bronze layer of that run.

| Column | Dictionary says | Data has | Decision | Why |
|---|---|---|---|---|
| `customers.country` | Mexico, Colombia, Argentina | México 74,907; Colombia 45,251; Argentina 29,842 | Normalize México to Mexico | Accented form of a documented value |
| `branches.country` | No list | México 175; Colombia 105; Argentina 70 | Normalize México to Mexico | Same spelling as `customers.country` and `service_agents.country_of_origin` |
| `transactions.transaction_country` | No list | México 2,105,794 and Mexico 40,515; also USA, Spain, Brazil | Normalize México to Mexico | Two spellings of one country in the same column |
| `customers.document_type` | DNI, CURP, CC, CE, Passport | DNI 104,749; CE 15,150; Pasaporte 15,062; CC 15,039; no CURP | Normalize Pasaporte to Passport | Spanish form of a documented value |
| `branches.geographic_zone` | Urban, Suburban, Rural | Urbana 350 (every branch) | Normalize Urbana to Urban | Spanish form; only the observed variant is mapped |
| `call_center_interactions.channel` | Phone, Web Chat, WhatsApp, Email, App | The five, plus Web 3,395 | Accept Web as an observed value | Not a variant of a documented value; mapping it to Web Chat would be a guess |
| `call_center_interactions.reason_category` | Transactional, Product, Technical, Commercial, Complaint (decoded) | Transaccional 240,056; Producto 150,863; Queja 117,021; Técnico 102,899; Comercial 54,879; Retención 20,578 | Normalize the five translations; accept Retención as observed | Retención has no documented counterpart, so it is kept as delivered rather than translated |
| `call_center_interactions.detected_sentiment` | Positive, Neutral, Negative, Very Negative | Neutral 459,712; Negativo 94,322; Positivo 75,562; Muy Negativo 37,727; Muy Positivo 18,973 | Normalize three translations; accept Muy Positivo as observed | Same rule as above |
| `products.product_type` | Truncated list ending in "Investme" | Cuenta Ahorro 120,203; Tarjeta Crédito 100,102; Cuenta Corriente 99,979; Tarjeta Débito 39,938; Préstamo Personal 19,960; Préstamo Hipotecario 11,910; Inversión 5,859; Seguro 2,049 | Normalize seven onto the decoded list (Inversión to Investment); accept Seguro as observed; bad values warn | The list is now checkable; Seguro may sit in the truncated tail, which we cannot see |
| `customers.registration_branch_id` | FK to branches, NOT NULL | 149,995 of 150,000 values absent from branches (each customer has a distinct id; 5 match) | Orphans warn, rows kept | The relationship is unusable as delivered; quarantining would empty the customer table. `products.opening_branch_id` resolves for all 400,000 rows |
| `service_agents.assigned_branch_id` | FK to branches | 831 of 833 non-null values absent from branches | Already warn; unchanged | Same pattern as customers |
| `call_transcripts.duration_seconds` | NOT NULL | 24,029 nulls (14.0%) | Null warns, rows kept | Not needed downstream; the duration also exists on the interaction |
| Partition folders `year`, `month`, `day` | Not columns | Added by DuckDB Hive auto-detection | Detection off; folders validated against `process_date` | 0 mismatches; they were never drift |

The first run's customer quarantine (149,995 orphans) was not a cascade from the quarantined branches: those branch keys were already part of the parent key set. The customers really point at branch ids that `branches.csv` does not contain. The non-cascading logic above is still in place, and after reconciliation no branch is quarantined.

### Observations reported, not changed

- **Row counts differ from the summary.** transactions 4,425,008 (summary 5,000,000); call_center_interactions 686,296 (800,000); satisfaction_surveys 212,759 (250,000); call_transcripts 171,321 (200,000); complaints 67,095 (80,000); daily_exchange_rates 13,164 (3,000). customers 150,000, products 400,000, branches 350, service_agents 1,200 and marketing_campaigns 200 match. `digital_events` and `campaign_sends` were not in the local mirror this first run read, but they are in the delivery: 1,097 daily files (3.76 GB) and 1,083 daily files (326 MB) under `data/` in the bucket. The mirror did not have them, so this first run never loaded them; they were loaded on 2026-09-27 (see below).
- **No duplicates.** No delivered table has a repeated key. Detection ignores `process_date` and ingestion columns, so a re-delivered record would count. A direct check on bronze also finds no rows that repeat the same content under a different id (transactions by customer, product, timestamp, amount and type; complaints by customer, timestamp, category and description; interactions by customer, timestamp and reason; surveys by customer, timestamp and interaction). The "~2%" in the summary is not reproduced.
- **Mexican accounts are in USD.** All 200,398 products and 2,216,431 transactions of Mexican customers are in USD; MXN never appears in products or transactions. Argentina uses ARS (792,585) and USD (87,420), Colombia COP (1,194,444) and USD (134,128). In total 2,437,979 transactions are in USD.
- **Exchange rates cover every directed pair.** 12 pairs among MXN, COP, ARS and USD for each of 1,097 days (2023-06-17 to 2026-06-17) give the 13,164 rows. The pair names the direction, which resolves that question from the dictionary.
- **Event time can be one day after the partition date.** In transactions, interactions and complaints the lag between `process_date` and the event date is -1 or 0 days (1,106,307 transactions have an event on the following day); surveys go down to -2. Within one partition, transaction timestamps span from about 06:00 to about 06:00 of the next day (hours are otherwise uniform), which is consistent with timestamps stored about six hours ahead of the local business day. That explanation is a hypothesis; the dictionary does not state a timezone. No row arrives late by the one-day rule.
- **Sparse or one-sided columns.** `transactions.transaction_category` is null in 60.9% of rows; `customers.detected_accent` is null in 29.9% and never "neutral"; `satisfaction_surveys.nps_category` has Detractor 45,007 and Passive 15,387 and no Promoter at all; `complaints.origin_interaction_id` is always null, so no complaint links back to a call.
- **Complaint categories are English and nearly uniform.** Transactions 13,580; Fees 13,553; Technical 13,407; Branch 13,361; Service 13,194. None is specific to an unrecognized charge; this matters for the workflow choice.
- **Uniqueness.** `service_agents.employee_code` repeats 13 values over 26 rows and `products.product_number` 6 values over 12 rows (reported, not quarantined).

After reconciliation the full refresh of these 11 tables quarantines 0 of 6,127,393 rows. Remaining warnings are the 149,995 and 831 branch orphans and the 24,029 null transcript durations. That run took 10.0 minutes on a laptop; about 80 seconds of it is per-file CSV header sniffing. With the two tables below, the warehouse holds 23,495,188 rows in 13 tables and none is quarantined (`reports/organizer_load.md`).

### digital_events and campaign_sends

Loaded on 2026-09-27 into the same warehouse (`--tables digital_events,campaign_sends`, after copying the two tables from the bucket into the local mirror with `freshness/stage.py`, the copy `make mirror` now runs), with the contracts already written from the dictionary. Nothing was changed in either contract.

| Table | Files | Rows in | Quarantined | Duplicates | Silver | Warnings | Orphans | Drift |
|---|---|---|---|---|---|---|---|---|
| digital_events | 1,097 (3.76 GB) | 15,620,994 | 0 | 0 | 15,620,994 | 0 | 0 of 11,875,548 customer ids, 0 of 1,440,338 product ids | none |
| campaign_sends | 1,083 (326 MB) | 1,746,801 | 0 | 0 | 1,746,801 | 0 | 0 of 1,746,801 customer ids and campaign ids | none |

Every value of the listed and decoded enums is in the contract (the decoded `event_type` list, ending in Purchase, is confirmed). Every file is UTF-8 with BOM, no event is late by the one-day rule, and the partitions run from 2023-06-17 (campaign sends from 2023-07-01) to 2026-06-17. Row counts differ from the summary in both directions: 15,620,994 digital events against 10,000,000, and 1,746,801 campaign sends against 2,000,000. Sparse columns: `digital_events.customer_id` is null in 24.0% of events (anonymous events are allowed), `product_id` in 90.8%; in `campaign_sends`, `open_date` is null in 72.1% and `conversion_date` in 99.4%.

The load took 37 minutes on a laptop, most of it on digital_events, which DuckDB processed in one transaction; during the run the process held at least 23 GB of memory and had spilled at least 19 GB to its temp directory (observed readings, not measured peaks). That table is now the capacity limit of the single-node design. No gold table, analytics figure or agent tool reads either table; regenerating `data_analytics/reports` after the load changed only the run ids in their provenance. Gold was rerun afterwards (every table skipped) so the tool repository accepts the warehouse again.

## Gold layer

```bash
make gold        # uv run python -m data_engineering.gold.run --target data/warehouse.duckdb (fixture)
make gold-real   # uv run python -m data_engineering.gold.run --target data/warehouse_real.duckdb (organizer data)
```

Gold is built from silver inside the same warehouse, in the `gold` schema. Each table has a contract in `gold/contracts/<table>.yaml` and one SELECT in `gold/sql/<table>.sql`. The workflow is unrecognized-charge disputes ("Cargo no reconocido"); the evidence for that choice is in `data_analytics/reports/why-this-workflow.md`.

| Table | Kind | Serves | Grain |
|---|---|---|---|
| `customer_profile` | row | `get_customer_profile` | one customer: country, segment, status, product and card counts; no contact details |
| `customer_transactions` | row | `list_recent_transactions`, `get_transaction`, `find_candidate_charges` | one transaction with merchant, channel and the product it moved |
| `customer_products` | row | product reads and the product ownership check (once the tool repository switches to it, see below) | one product with owner, type, number, currency and status |
| `dispute_policy_inputs` | view | `get_dispute_policy` | one transaction with the customer country and segment and the product state the policy rules read |
| `complaint_facts` | row | analytics, ML | one complaint with the workflow label, country, segment, timing and measured repeat contact |
| `complaint_outcomes` | aggregate | analytics | complaint type by overall, country, segment and reception channel: SLA breach, escalation, resolution time, repeat contact |
| `interaction_outcomes` | aggregate | analytics | contact reason by overall, country, segment and channel: FCR, escalation, follow-up, handling time |
| `demand_by_hour` | aggregate | analytics | contacts by source, workflow, country, channel, weekday and hour |
| `demand_by_day` | aggregate | analytics | contacts by source, workflow, country, channel and calendar day |
| `workflow_selection` | view | analytics | one complaint type with volume, share and outcomes, ranked by volume |

The agent tools in `agent/tools/repository.py` read `customer_profile`, `customer_transactions` and `dispute_policy_inputs`, and refuse to start when gold is missing or older than silver. They still read `silver.customers` for the identity directory, which gold leaves out on purpose, and `silver.products` for products and product ownership. `customer_products` is the gold table for those product reads: one row per silver product with the columns the repository reads, the same blocking checks as every gold table, and a reconciliation that fails the build when any owner, type, number, currency or status differs from silver. The freshness status covers it like any materialized table. `tests/gold/test_gold_products.py` checks that it answers each repository product query exactly as silver does. It is not built on the organizer warehouse yet (the next `make gold-real` builds it), and the repository has not been switched to it.

**Workflow label.** `is_unrecognized_charge` matches the literal "Cargo no reconocido" in `category` or `subcategory`. The organizer data puts it in `subcategory` under category "Transactions" (12,297 complaints); the fixture puts it in `category`. The 1,283 "Transactions" complaints without a subcategory are not counted, since nothing says what they are.

**Contracts are enforced, not documented.** Before a table is written, the build describes the SQL result and refuses it when a column is missing, extra, or of another type than the contract says. After writing, and inside the same transaction, `gold/checks.py` checks the primary key (unique, not null), NOT NULL columns, value lists, numeric ranges, lineage on every row, and the contract's reconciliation queries (for example: one gold row per silver transaction, complaint types adding up to the silver total, every breakdown adding up to its overall row). Gold is derived data, so every check blocks: a failure rolls the table back and the run fails. The run writes `data/reports/gold_<run_id>.json` with the mode, row counts and every check result per table.

**Lineage.** Every gold row carries `_source_table` (the silver or gold table it comes from), `_source_key` (the primary key for row tables, the group key for aggregates), `_source_run_ids` (the silver `_run_id` of every silver row that contributed, so a joined row lists the transaction's and the product's runs) and `_gold_run_id`. `control.gold_runs` records each run.

**Incremental and idempotent.** `control.gold_state` keeps, per gold table, the definition hash (contract plus SQL) and, per silver source, the highest `_ingested_at` and the row count seen at the last build.

| Situation | What the build does |
|---|---|
| Nothing changed | Skips the table; a rerun changes nothing |
| First build, or contract or SQL changed | Full build |
| Row table, a source moved | Each contract lists, per source, the query that finds the gold keys touched since the old watermark; those keys are deleted and re-inserted, and keys that left silver are deleted |
| Row table, a source moved unsafely | Full build. A source whose high-water mark did not advance, that lost rows, or whose every row was reloaded (a silver full refresh, the only way silver drops keys) cannot be patched by key: the key queries only see new rows |
| A gold input was rebuilt | Full rebuild of what reads it; a gold source is marked by the run that last changed it |
| Aggregate, a source moved | Full rebuild; these tables are small and their percentiles cannot be patched |
| View | Recreated every run |

A new complaint changes the prior and next complaint features of the same customer's other complaints, so `complaint_facts` recomputes every complaint of a touched customer. `tests/gold/test_gold_incremental.py` loads the fixture in the same two waves as the silver test, builds gold after each, and requires the result to equal gold built once over a full load, row for row, apart from the run ids.

**Freshness policy.** Gold runs after every silver load (`make pipeline` then `make gold`, or `make pipeline-real` then `make gold-real`), and the tool repository refuses to start when gold is older than silver. The policy tools compute transaction age at request time, which is why no age is stored. The full policy, including what happens after a rewritten file, is in the freshness section below.

**Result on the organizer data.** First build 32 s on a laptop, rerun 3 s (every table skipped). Rows: 4,425,008 transactions, 150,000 customer profiles, 67,095 complaint facts, 154 complaint outcome rows, 98 interaction outcome rows, 32,053 hour cells and 111,771 day cells. Every check passes.

**Findings the gold build surfaced.** `is_repeat_complainer` does not match the complaint history: of 10,086 flagged complaints, 333 have an earlier complaint within 90 days, while 2,315 complaints have one. `sla_breached` does not follow resolution time (median 15 days when breached, 16 when not). `resolution_days` exists only for Resolved and Closed complaints. The claimed-amount currency of a complaint does not follow the customer's country (Argentine customers file in MXN and COP), and MXN appears in complaints although no product or transaction is in MXN. These are reported in the analytics, not corrected.

## Freshness and update policy

```bash
make freshness-status TARGET=data/warehouse_real.duckdb   # uv run python -m data_engineering.freshness.status --target ...
make freshness-demo                                       # update test on organizer data, writes reports/freshness_backup_vs_current.md
```

**Cadence and staleness thresholds.** The contract's `partitioning` sets the expected cadence, and `freshness/status.py` (`MAX_LAG_DAYS`) holds the limits.

| Cadence | Tables | Measured by | Stale when |
|---|---|---|---|
| Daily partitions | transactions, call_center_interactions, call_transcripts, complaints, satisfaction_surveys, daily_exchange_rates, digital_events, campaign_sends | newest `process_date` in silver (rate date for exchange rates) | more than 1 day behind the reference date. Partition D is expected by D+1, because event times reach at most one day past the partition date in the organizer data |
| Monthly snapshot | customers, products, service_agents | modification time of the newest loaded file (snapshots carry no snapshot date) | more than 35 days behind |
| Full snapshot | branches, marketing_campaigns | same | never: the dictionary gives no cadence, so the status is `no_policy` |

The reference date is `--as-of` (today in UTC by default). For a daily table the status also gives the lag behind the dataset clock, the newest partition of any daily table in the warehouse, which is the useful number while the data is a static delivery: measured against the calendar, every daily table of the organizer data is months stale. `freshness-status` exits with code 1 when a table is stale or not loaded, or when gold is stale. Snapshot tables loaded before the ledger recorded modification times report `no_date` until their file is loaded again (the load time is not used in its place, because it says nothing about when the data was produced).

**Gold.** Gold runs after every silver load. It is stale when its latest successful run is older than the latest successful silver run, or when a materialized table's recorded source marks no longer match silver. After a rewritten file, every gold table that reads the affected silver table is rebuilt in full instead of patched by key, because a rewrite can withdraw keys that the key queries cannot see.

**Fail-closed serving.** The tool repository (`agent/tools/repository.py`, `check_gold_ready`) refuses to start when a gold serving table is missing or when the latest successful gold run is older than the latest successful silver run, so the agent never answers from gold that lags silver. The check runs when the repository opens the warehouse. The calendar thresholds above are reported, not enforced by the repository: with a static delivery they would keep the service down permanently. Transaction age is computed at request time by the policy tools.

**Changed files.** The ledger compares each file's size and modification time with the version it loaded.

| Situation | What the pipeline does |
|---|---|
| New file | Loaded |
| Same size and time | Not read |
| Size or time changed | The old version's rows leave bronze, silver and quarantine; the new version is loaded; keys whose silver row came from the old version and that still have a version in another file compete again (replayed from bronze, not re-quarantined); the old ledger row is kept with `superseded_by_run` |
| Ledger row from before fingerprints existed | Treated as unchanged, so an upgrade does not reload the whole warehouse |
| File no longer in the source | Counted as `missing_from_source`; its rows are kept. A listing that comes back short should not empty a table |

The rewrite path is tested on the fixture in `tests/test_freshness.py`: seed 42 followed by seed 43 converges to a single load of seed 43 (bronze, silver, gold, quarantine and ledger); a rewritten partition that changes one row and withdraws another matches a single load of the final files; a withdrawn winner brings back its older version from another file; gold rebuilds in full and matches a single load.

**Update test on organizer data.** The bucket holds the current delivery under `data/` and an earlier generation of the dataset under `data_backup_20260831/`. `make freshness-demo` loads the earlier generation, overwrites the landing directory with the current one and loads again into the same warehouse, then compares it with a warehouse built in one load. The earlier generation is not an incremental predecessor: 2,650 of the 2,653 paths the two states share hold different bytes and most keys were regenerated, so the run exercises the rewrite path at full volume (2,653 files rewritten, 2,838 new, 1,839,229 transaction rows and 581,513 quarantined interaction rows withdrawn) rather than a daily increment. The updated warehouse matched the single load in all 31 compared objects, every gold table that reads a rewritten table was rebuilt in full, and a rerun read nothing. Replay was not exercised there (every file of a table was rewritten); the fixture tests cover it. Full numbers and what the run does not prove: `reports/freshness_backup_vs_current.md`.

One conservative effect showed up in that run: a silver run that loads nothing still counts as a newer silver run, so the repository refuses to start until gold runs again. Gold then skips every table in a few seconds.

## Data classification and data at rest

Every column of the 13 silver contracts and of the gold contracts carries a `classification`. The loaders refuse a column without one, and `tests/test_classification.py` checks the classes below.

| Class | Rule | Examples |
|---|---|---|
| `pii_direct` | Identifies a person on its own, or is free text a person wrote or said | document number, first and last name, email, phones, address; call transcripts, complaint descriptions and resolutions, survey comments; agent names and contacts |
| `pii_quasi` | Identifies a person only in combination with other data, or links a row to a person | customer and agent ids, employee code, date of birth, gender, city, state, postal code, accent, occupation, marital status, education, IP address and IP location, session id, transaction city and coordinates |
| `sensitive_financial` | An account or card number, balance, limit, rate, income, credit or fraud score, or a money amount of a person | product number, balance, credit limit, interest rate, days past due, credit score, monthly income, transaction and claimed amounts |
| `none` | Everything else | statuses, categories, event dates, bank reference data (branches, campaigns, exchange rates) |

Of the 260 silver columns, 18 are `pii_direct`, 36 `pii_quasi`, 15 `sensitive_financial` and 191 `none`. The gold serving tables hold two direct identifiers, the customer's first and last name in `customer_profile`: the agent uses them for the "First L." display name and to mask the name in free text. A test fails if any other direct identifier reaches a gold table, or if a gold column copied from silver carries a weaker class than its source. The classification changes no row, so it is left out of the gold definition hash and adding it forced no rebuild.

**What is stored where.** The classification is recorded, and storage does not act on it yet: nothing is masked, tokenized or encrypted at rest in this build.

| Location | What it holds, in clear | Who can read it |
|---|---|---|
| `data/raw/` | The delivered CSV files: every column, including document numbers, names, dates of birth, email, phones, addresses, transcripts and complaint texts | Anyone with access to the machine account that ran `make mirror`. Git-ignored, never committed |
| `data/warehouse_real.duckdb`: bronze and silver | Every delivered column, as text in bronze and typed in silver | Same. The DuckDB file is not encrypted |
| `quarantine.records` | The raw record of every quarantined row, as received (0 rows in the current organizer warehouse) | Same |
| gold | Customer names, state and city; customer ids, amounts and fraud scores; no document number or contact details | Same. The agent opens the warehouse read-only |
| Other files under `data/` (freshness caches and scratch warehouses, eval slices) | Copies of the same organizer rows | Same |
| `data/reports/*.json` | Counts, null rates and value profiles of listed columns (document type, country, accent, marital status, education) | Same. The committed files in `reports/` hold aggregates only |
| The demo slice on the VPS (`warehouse.duckdb` in the demo bundle) | Bronze and silver customers, products and transactions of the eight demo customers, with names, document numbers, dates of birth and contact details, plus their gold serving rows | The container that serves the demo, and anyone with shell access to the VPS host, where the bundle tarball is copied and unpacked (`deploy/README.md`) |

Today the protection is on the serving path: tools take the customer from the session and never from their arguments, the warehouse is opened read-only, and document, email and phone are masked before they reach the model, the audit log and the handoff (top-level `SECURITY.md`).

**Production alternative.** In a bank deployment the classification would drive storage. Direct identifiers would leave the analytical layers: `document_number` stored as a keyed HMAC in silver (the identity lookup already normalizes the document before comparing, so it can compare keyed hashes instead), and names and contact details kept in an identity store that only the identity service reads, or behind column-level masking policies by role in the warehouse engine. The warehouse files, the landing copy and the bucket would be encrypted at rest with keys held in a key management service, quarantine raw records would carry a retention limit, and the demo slice would carry synthetic names, documents and contacts. None of this is built here.

## Capacity limits and route to production

DuckDB runs on one machine. The organizer dataset (23,495,188 rows, the largest table digital_events at 15.6 million) fits on a laptop, but loading digital_events in one transaction held at least 23 GB of memory plus a temp spill; splitting a table's first load into several transactions is the next step if the delivery grows. The fixture run takes a few seconds. Past what one machine's disk and memory hold, the same SQL can run on Databricks or Snowflake with the ledger and watermark tables kept as they are. Other known limits: a rewrite is detected by size and modification time, not by content, so a file re-uploaded with identical bytes is reloaded (the result does not change, the run just costs more); a file that disappears from the source is counted as `missing_from_source` but its rows are kept; rows quarantined as orphans are not replayed automatically when their parent arrives later; deletions in snapshot tables are not propagated; and VARCHAR lengths are not enforced.

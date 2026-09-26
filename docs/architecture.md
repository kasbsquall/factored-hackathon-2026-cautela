# Cautela architecture

Status: design draft, day 1. Decisions marked **provisional** wait on the exploratory analysis of the supplied data.

## 1. Principle

The language model interprets and explains. Deterministic code decides what is allowed and what happened.

Every action the system reports was verified by reading state back from the tool layer. Every refusal or transfer cites a rule or a missing fact. None of this depends on hidden model reasoning.

## 2. Request lifecycle

```
customer message (es / pt)
   │
   ▼
[1] Session gate ─────────── no valid test session → authenticate or stop
   │
   ▼
[2] Understand ───────────── LLM extracts a typed DisputeIntent (schema-validated);
   │                         PII masked before any external model call
   ▼
[3] Decide ───────────────── learned decision component proposes: resolve / clarify / escalate
   │                         policy engine (deterministic) can only narrow it, never widen it
   ▼
[4] Act ──────────────────── tool call through the permission layer (per-customer scope,
   │                         per-action allowlist, confirmation required where policy says so)
   ▼
[5] Verify ───────────────── read state back; report success only if state matches
   │
   ▼
[6] Escalate (when needed) ─ structured handoff JSON to the human-agent console
```

Every step writes an execution record (trace id, inputs, rule ids, tool calls, outcome) to the audit log.

## 3. Components

### 3.1 Data engineering (`data_engineering/`)

- **Medallion layout.**
  - Bronze: the supplied tables as received.
  - Silver: deduplicated, typed and contract-checked, with late arrivals handled by `process_date`.
  - Gold: serving tables for the tools, plus the analytics and ML datasets.
- **Contracts.** One schema per table: types, nullability, allowed values and foreign keys, taken from the data dictionary. A failing row is quarantined with its reason, never silently dropped.
- **Quality report.** Duplicate rate, null rate, orphan rate and schema drift for each run, with rows in and rows out per step.
- **Lineage.** Each gold row keeps its source table, source key and run id.
- **Freshness and update policy.** The data is delivered as static files, so update correctness is shown with a labeled incremental test fixture, as the problem statement allows.
- **Engine (provisional).** DuckDB over Parquet. Why: it reproduces on any laptop with one command and costs nothing, and SQL stays portable to Databricks or Snowflake. Trade-off: single node. That is enough for 19M rows and is documented as a capacity limit.

### 3.2 Serving data and mock banking tools (`agent/tools/`)

These are mock tools over the gold serving tables (`gold.customer_profile`, `gold.customer_transactions`, `gold.dispute_policy_inputs`; read-only connection). The identity directory and products are still read from silver: gold keeps no contact details by design and has no product-level table yet. Writes go to a separate sandbox case store and never modify source data. Each tool has a documented contract (input schema, output schema, errors, side effects) exported to `docs/schemas/tools/`. Every call goes through one entry point, `agent/service.py`. Details and the reasons behind each control: [agent/README.md](../agent/README.md).

| Tool | Kind | Notes |
|---|---|---|
| `get_customer_profile` | read | Scoped to the session's customer only |
| `list_recent_transactions` | read | Session customer, bounded window |
| `get_transaction` | read | Rejects ids that belong to another customer |
| `find_candidate_charges` | read | Ranks own charges against the customer's amount, date and merchant hints; never a single guess when ambiguous. `complaints` has no `transaction_id`, so the link is inferred |
| `get_dispute_policy` | read | Returns the rule set that applies (country, product, channel, age of transaction) |
| `open_dispute_case` | write | Idempotency key and confirmation required; returns the case id |
| `block_card` | write | Requires explicit customer confirmation |
| `get_case_status` | read | Used by the verify step |

Failure injection (timeouts, 5xx, stale reads) can be switched on for evaluation. Retries are bounded, followed by a safe fallback to a handoff.

### 3.3 Identity (`agent/security/session.py`)

This is a test identity service that issues signed, expiring sessions for synthetic customers. A document number alone never grants access: it only starts a challenge, and a one-time code sent to the registered channel (mocked) completes the login. Expired, tampered, revoked or replayed sessions are refused. This covers the "expired sessions" and "unauthorized access" evaluation cases.

### 3.4 Policy engine (`agent/policy/`)

The policy engine is plain code. It holds:

- dispute eligibility windows,
- amount thresholds that force human review,
- which actions need confirmation,
- which requests are out of scope,
- the fraud-score level that forces escalation,
- the reason codes for each transfer, taken from the handoff schema.

Each rule has an id, a source and a verification status in `agent/policy/rules.yaml`. The source is a primary legal text (Mexico LTOSF art. 23, Colombia Decreto 587 de 2016, Argentina Ley 25.065 arts. 26-28 and BCRA rules) or a clearly labeled synthetic policy where no approved bank policy was supplied. The model can cite rule ids but cannot change them: a model proposal can only drop actions or add escalation.

### 3.5 Learned decision component (`ml/`)

- **Task.** Given the intent and the verified facts, predict the right disposition: `resolve`, `clarify` or `escalate`.
- **Candidates (provisional ladder), all evaluated on the same held-out set:**
  1. Deterministic rules, which serve as the baseline.
  2. A classical model (TF-IDF or embeddings plus logistic regression).
  3. A prompted LLM with a versioned prompt and a structured output.
- **Labels.** Derived from historical outcomes in `complaints` and `call_center_interactions` (resolution, escalation, SLA). How reliable these labels are is analyzed and reported before training. **Provisional** until the data is inspected.
- **Leakage control.**
  - Splits are by customer and by time, so no customer appears in both train and test.
  - Translated Portuguese cases stay in the same split as their Spanish source.
- **Tracking.** MLflow (local) records parameters, prompt version, metrics and artifacts for every run.
- **Thresholds.** A calibrated confidence decides between acting and asking. The threshold is chosen on validation, never on test, and results are reported by confidence bucket.

### 3.6 Handoff (`docs/schemas/handoff.schema.json`)

The handoff is structured JSON with these fields: the request, the verified facts with their sources, the actions taken and their verified status, the evidence, the open questions, the reason for the transfer, the language, and the trace id. Brazil's consumer-service rules forbid making the customer repeat the request after the first contact (Decree 11.034, art. 10), which is the business reason for this format.

### 3.7 Language

- **Spanish** comes from the supplied transcripts and complaints.
- **Portuguese** is team-generated from held-out Spanish scenarios and reviewed. It is labeled as generated and reported as a limitation, because the supplied data has no Portuguese and no Brazil.

### 3.8 Interfaces (`app/`)

- The customer conversation.
- The human-agent console that receives handoffs.
- An audit view: the trace for one case, with rule ids, tool calls and verification results.

### 3.9 Observability and operations

- A trace id for every request.
- Structured logs.
- Latency and cost recorded per step.
- Bounded retries with a safe fallback.
- A data retention policy for the audit log (`AUDIT_RETENTION_DAYS`, synthetic, 10 years) and the transcripts.
- Hash-chained, append-only audit records with masked arguments and a keyed hash of the raw ones.
- A reproducible setup with one command.

## 4. Evaluation plan (summary)

Held-out cases cover:

- normal cases,
- ambiguous cases,
- human-required cases,
- incorrect or missing data,
- expired sessions,
- unauthorized access,
- prompt injection in Spanish and Portuguese,
- tool failures,
- multilingual ambiguity.

The metrics follow the problem statement definitions. Each one is reported with its sample size, broken down by language and by customer segment, with the variance across repeated runs for the LLM components.

## 5. Open decisions

- The workflow is confirmed only after the analysis of `contact_reason` and `complaints.category`.
- The label definition for the decision component.
- The hosting target (free tier) for backend and frontend.

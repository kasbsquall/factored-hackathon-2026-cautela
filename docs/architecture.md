# Cautela architecture

What is built, in one page. Each section links to the README that holds the detail, the tests and the numbers.

## 1. Principle

The language model interprets and explains. Deterministic code decides what is allowed and what happened.

Every action the system reports was verified by reading state back from the tool layer. Every refusal or transfer cites a rule or a missing fact. None of this depends on hidden model reasoning.

## 2. Request lifecycle

```
customer message (es / pt)
   |
   v
[1] Session gate ....... expired, revoked or forged token -> refused, log in again
   |
   v
[2] Security screen .... injection markers, another customer's record -> security_event handoff
   |
   v
[3] Understand ......... parser plus masked LLM extraction into a DisputeIntent (schema-checked);
   |                     every extracted value must appear in the message; the parser wins on amount,
   |                     date and currency when the two disagree
   v
[4] Decide ............. learned ranker and disposition model over the customer's 90-day charges:
   |                     act on one charge, clarify (up to 3 numbered candidates, at most two rounds),
   |                     or hand off; the policy engine can only narrow the proposal
   v
[5] Recognize .......... the charge as the tools read it plus the match reasons; "I recognize it"
   |                     closes the conversation with nothing written
   v
[6] Confirm and act .... confirmation token bound to session, tool and exact arguments, kept in
   |                     server state; then open_dispute_case (or block_card)
   v
[7] Verify ............. read the case back; success only if it matches, else a handoff
   |
   v
[8] Escalate ........... structured handoff JSON to the human-agent console
```

Routing runs the same order at every stage, including while a question is pending (security screen, a request for a person, quoted records, out of scope, a new description). Every step writes a hash-chained audit record under the turn's trace id (rule ids, tool calls, outcome, latency, model tokens and cost). Step by step with file names: [agent/README.md](../agent/README.md), section "Orchestrator".

## 3. Components

### 3.1 Data engineering (`data_engineering/`)

Medallion on DuckDB: bronze keeps every column as text with source file, ingestion time and run id; silver types, checks, deduplicates and upserts; gold serves the tools (`customer_profile`, `customer_transactions`, `dispute_policy_inputs`) and the analytics. One contract per table (13) from the data dictionary, with provenance per column. A failing row goes to quarantine with its reason codes, never silently dropped. Loads are incremental and idempotent (file ledger and watermarks), and every gold row keeps its source table, source key and run ids. DuckDB reproduces on a laptop at no cost and the SQL ports to Databricks or Snowflake; the trade-off is a single node. Detail: [data_engineering/README.md](../data_engineering/README.md).

### 3.2 Serving data and mock banking tools (`agent/tools/`)

Eight tools (six reads, two writes) over the read-only gold serving tables. The identity directory and products are still read from silver: gold keeps no contact details by design and has no product-level table. Writes go to a separate sandbox case store. Each tool has a pydantic contract exported to `docs/schemas/tools/`, and every call goes through one entry point, `agent/service.py`, whose guard checks allowlist, session, replay, contract, ownership, policy and confirmation in that order. The repository refuses to start when gold is missing or older than silver.

| Tool | Kind | Notes |
|---|---|---|
| `get_customer_profile` | read | Scoped to the session's customer only, masked |
| `list_recent_transactions` | read | Session customer, at most 90 days and 50 rows |
| `get_transaction` | read | Rejects ids that belong to another customer |
| `find_candidate_charges` | read | Ranks own charges against the customer's hints. `complaints` has no `transaction_id`, so the link is inferred |
| `get_dispute_policy` | read | The rule set that applies (country, product, channel, age of the charge) |
| `open_dispute_case` | write | Idempotency key and confirmation required; returns the case id |
| `block_card` | write | Confirmation required; own active cards only |
| `get_case_status` | read | Used by the verify step |

Failure injection (timeout, error, stale read, lost write) can be switched on for evaluation. Retries are bounded, followed by a `tool_failure` handoff. Detail: [agent/README.md](../agent/README.md).

### 3.3 Identity (`agent/security/session.py`)

A test identity service for synthetic customers. A document number alone never grants access: it starts a challenge, and a one-time code sent to the registered channel (mocked) completes the login. Sessions are signed and expire; tampered, revoked or replayed ones are refused. Controls and their status: [SECURITY.md](../SECURITY.md).

### 3.4 Policy engine (`agent/policy/`)

Plain code over `agent/policy/rules.yaml`: claim windows per country, the USD 450 review threshold, the fraud escalation level, which actions need confirmation, which requests are out of scope, disputable statuses and the fixed FX rates for charges without a USD amount. Each rule has an id, a source (a primary legal text or `synthetic_policy`) and a verification status. A model proposal can only drop actions or add escalation (`narrow`). Rule table and sources: [agent/README.md](../agent/README.md), section "Policy".

### 3.5 Learned decision component (`ml/`)

- **Task.** Given the description and the customer's 90-day pool, act on one charge, clarify, or abstain.
- **Labels.** The supplied text cannot label this task (templated, no link from complaint to transaction), so the scenarios are real organizer transactions with team-generated descriptions rendered from structured hints. The label (`match`, `ambiguous`, `no_match`) is an explicit rule of the hints and the pool, recomputed for every case by a test.
- **Ladder, all on the same cases.** Rules with a fixed clarify rule, rules with a calibrated confidence and val thresholds (baseline), a learned ranker with the same decider (ablation), the learned ranker plus a case-level disposition model (proposed), and a prompted LLM ranker (gpt-6-luna, prompt `rank_v1`, calibrated and thresholded the same way). No embeddings or TF-IDF: the decisive cues are numbers, dates and short names, read by a deterministic parser.
- **Leakage control.** Group split by customer, time split by report date, held-out template families only in test, Portuguese in the same split as its Spanish source, calibrators and thresholds fitted only on train and validation, and a one-shot `test_fresh` split frozen by sha256.
- **Result.** On `test_fresh` (1,240 cases) the proposed system makes 89.8% correct decisions against 82.2% for the baseline, with 4 unsafe outcomes against 24. The LLM ranker reaches 97.2% top-1 on the original test split, but its scores support act or abstain decisions poorly (54.0% correct against 93.9% for the learned system), so the LLM does not decide which charge.
- **Tracking.** MLflow in a SQLite store under the git-ignored `mlruns/`.

Detail: [ml/README.md](../ml/README.md), [ml/reports/results_llm.md](../ml/reports/results_llm.md).

### 3.6 LLM port (`agent/llm/`)

Provider-agnostic: Anthropic, OpenAI-compatible (OpenAI, Groq, Gemini, Ollama) and a fake adapter for tests. Adapters accept only a prompt the port has masked. The model is used for three things: extraction in the Understand step, wording of replies (which must pass a grounding check or a template is used), and English translation of conversation messages for reviewers (`POST /conversations/{id}/translate`). The deployed service uses gpt-6-luna with reasoning effort none, chosen in a probe of three candidates on 50 validation cases (`ml/reports/probe/`). Each call logs tokens, latency and estimated cost from `agent/llm/prices.yaml`; the deployed process has a daily cap (2000 calls and USD 1.00 by default) and falls back to the parser and templates when it is reached. Without a configured provider the service runs fully deterministic.

### 3.7 Handoff (`docs/schemas/handoff.schema.json`)

Structured JSON: the request, the verified facts with their tool and record source, the actions taken with their verified status, rule citations as evidence, open questions, the reason code, the language and the trace id. No raw transcript. Brazil's consumer-service rules forbid making the customer repeat the request after the first contact (Decree 11.034, art. 10), which is the business reason for this format. The builder refuses a document that fails the schema.

### 3.8 Language

The organizer data is Spanish only and has no Brazil. The dispute descriptions used for training and evaluation are team-generated in Spanish; Portuguese, code-switching and regional slang are team-generated too and were not reviewed by native speakers of each variant. Both are labeled as generated and reported as a limitation.

### 3.9 API and interfaces (`api/`, `app/`)

- **API.** FastAPI over the orchestrator, server-driven: the client sends turns, recognition answers and confirmations and never calls a tool. Typed errors that never echo input, rate limits, console routes behind `X-Console-Key`. Detail: [api/README.md](../api/README.md).
- **Frontend.** Next.js App Router: the customer conversation (es and pt, with an English reviewer view), the human-agent console that receives handoffs, the audit trail with rule ids, tool calls, verification and the hash chain, and `/insights` with the data behind the workflow choice. Live mode reaches the API through a same-origin Next proxy; mock mode runs the same client contract in the browser.

### 3.10 Deployment and operations

- **Hosting.** The API runs on a shared VPS in one container published on `127.0.0.1:8330` only, behind OpenLiteSpeed with TLS: read-only root, non-root user, all capabilities dropped, 640 MB and 0.75 CPU. It serves a demo bundle (learned model and a slice of eight held-out organizer customers) checked against `deploy/demo-bundle.lock.json` at build and at every start, and resets every 30 minutes. `deploy/audit.sh` runs after each deploy. The frontend is on Vercel. Runbook: [deploy/README.md](../deploy/README.md).
- **Observability.** A trace id per turn; per-step latency, tokens and cost in the trail; `/health` with the LLM provider and model, the disposition model and, in the deployed process, the verified bundle and the LLM budget.
- **Audit and retention.** Hash-chained JSONL per UTC day with masked arguments plus a keyed hash of the raw ones. `AUDIT_RETENTION_DAYS` is 3650 (synthetic, mirrors BCRA PUSF 3.1.3); the public demo deletes the trail at every reset.
- **Reliability.** Bounded retries, read-back verification, a `tool_failure` handoff, and the deterministic fallback when the model or its budget fails.

## 4. Evaluation

- **Component.** The charge matcher on the case splits above (`ml/reports/results.md`, `results_fresh.json`, `results_llm.md`).
- **End to end.** `eval/` runs frozen suites of scripted conversations through the orchestrator, with sandbox services over a read-only slice of the organizer warehouse, judged by a deterministic judge (`eval/judge.py`) with an independent oracle for the policy. The first suite (1,462 conversations: disputes, recognized, bad data, expired session, tool failure, multilingual, human request, out of scope, unauthorized, injection, adversarial, identity) compares rules, learned and learned plus gpt-6-luna, and was used for error analysis: [eval/report.md](../eval/report.md). A second suite, `eval_fresh` (1,692 conversations), was frozen before any fix and runs once at code freeze: [eval/fresh/DATASHEET.md](../eval/fresh/DATASHEET.md).
- Metrics follow the problem statement definitions, with sample sizes, intervals and breakdowns by language, country and segment; the LLM configuration also reports variance over repeated runs and spend. Definitions: [README.md](../README.md), section "Evaluation".

## 5. Decisions taken

The day-1 draft left these open. Each is now settled, with the evidence in the linked file.

| Decision | Outcome | Evidence |
|---|---|---|
| Workflow | Disputes of unrecognized charges: the largest complaint type (tied within sampling error) and checkable against the customer's own transactions | [data_analytics/reports/why-this-workflow.md](../data_analytics/reports/why-this-workflow.md) |
| Label definition | Generated scenarios over organizer transactions, label valid by construction from the hints and the pool | [ml/README.md](../ml/README.md), [ml/DATASHEET.md](../ml/DATASHEET.md) |
| Engine | DuckDB, medallion with gold serving tables | [data_engineering/README.md](../data_engineering/README.md) |
| Model for language tasks | gpt-6-luna through the port, for extraction, replies and reviewer translation; the learned model and the policy decide | `ml/reports/probe/`, [ml/reports/results_llm.md](../ml/reports/results_llm.md) |
| Hosting | API on a VPS behind OpenLiteSpeed, frontend on Vercel | [deploy/README.md](../deploy/README.md) |

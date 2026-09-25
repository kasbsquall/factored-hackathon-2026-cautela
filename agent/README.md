# Agent core: identity, permissions, policy, tools, audit and handoff

This package holds every control that decides what Cautela may read or do. It is plain Python with pydantic
contracts, so it runs and is tested without a web framework; a FastAPI layer can wrap `ToolService` later without
moving any enforcement. The language model is treated as untrusted: it proposes tool calls, and this code decides.

```bash
uv sync
uv run pytest tests/agent                     # 178 tests on the synthetic fixture warehouse, about 40 s
uv run python -m agent.tools.export_schemas   # rewrite docs/schemas/tools/*.json from the contracts
```

`SESSION_SECRET` must be set in `.env` (git-ignored, at least 32 bytes) before running the service outside tests.
Generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Tests use a random secret per run.

## Layout

| Path | What it does |
|---|---|
| `security/signing.py` | HMAC-SHA256 signing with one derived key per purpose (session, confirmation, audit, idempotency) |
| `security/session.py` | Test identity service: document number starts a challenge, a one-time code finishes it, signed expiring sessions |
| `security/permissions.py` | The guard every tool call passes, plus single-use confirmation tokens |
| `security/audit.py` | Trace ids and hash-chained, append-only execution records (JSONL per day) |
| `security/pii.py` | PII masking for anything that leaves the service |
| `policy/rules.yaml`, `policy/engine.py` | Deterministic rules with ids and sources; `narrow()` for model proposals |
| `tools/contracts.py`, `tools/registry.py` | Pydantic input and output contracts, error codes, the tool allowlist |
| `tools/impl.py`, `tools/repository.py` | Tool logic over the read-only silver warehouse and a sandbox case store |
| `tools/ranking.py` | `CandidateRanker` protocol, rule-based default ranker, ambiguity rule |
| `tools/faults.py` | Failure injection and bounded retries with backoff |
| `service.py` | `ToolService`: the single entry point (guard, execute, verify, audit) |
| `handoff.py` | Handoff builder validated against `docs/schemas/handoff.schema.json` |

## Controls and the requirement each one answers

Quotes are from the problem statement. Each control is enforced in code and covered by tests in `tests/agent/`.

| Control | Requirement | Why it is built this way | Tests |
|---|---|---|---|
| Two-step login, signed sessions | "a national ID or customer number alone does not prove identity" | A document number only opens a challenge; the code goes to the registered channel. Unknown documents get the same response shape, the same work and no code, so login is not an enumeration oracle. At most 3 challenges per document every 15 minutes, 3 attempts each, so the 6-digit code cannot be brute-forced across fresh challenges. | `test_session.py` |
| Session expiry, tamper and replay checks | "Include ... expired sessions, unauthorized access attempts" | Tokens carry an expiry and a signature over the customer id; request ids are single-use per session; logout revokes. | `test_session.py`, `test_permissions.py` |
| Ownership on every record reference | "Enforce access to each customer's records and action permissions in the service or tool layer" | Tools never accept a customer id (unknown fields are rejected); every record id argument is checked against the session customer before the tool runs. | `test_permissions.py` (every tool) |
| Tool allowlist, deny by default | "Enforce permissions and policy outside model-generated prose" | A tool that is not registered cannot run, whatever the model writes. There is no refund, SQL or status-update tool. | `test_permissions.py`, adversarial cases |
| Confirmation tokens | "Define ... which actions require confirmation" | Actions listed in `rules.yaml` need a token signed over session, tool and a digest of the exact arguments; it expires in 5 minutes and is consumed once. The token goes to a UI button, never into model context. | `test_permissions.py` |
| Policy engine, narrow only | "The conversational model must not invent ... rules" (credit clause, applied here to disputes) | Rules decide allowed actions, confirmations and escalation from verified facts. `narrow()` lets a model drop actions or add escalation and reports anything else as rejected. | `test_policy.py` (includes 300 random proposals) |
| Verify step | "report only actions whose outcomes the system has verified" | After each write the tool reads the record back. A stale read or a lost write returns `not_verified` with a handoff, never a success. | `test_tools.py` |
| Bounded retries, safe fallback | "Demonstrate tracing, bounded retries, safe fallback" | Three attempts with 50, 100 ms backoff on transient errors; then `tool_unavailable` with `handoff_required`. Writes are idempotent, so a retry never duplicates a case. | `test_tools.py` |
| Audit records per step | "Provide explanations based on sources, policy rules, and execution records; hidden model chain-of-thought is not an audit artifact" | Each step records trace id, tool, keyed args hash, masked args, rule ids, outcome, reason and latency, including every denial. Records are hash-chained so edits and deletions are detectable. | `test_pii_audit.py`, `test_permissions.py` |
| PII masking | "Do not include private customer records ... in ... external model requests" | Emails, card and account numbers, phones, document numbers and names are masked before text goes to a model, the audit log or a handoff. | `test_pii_audit.py`, `test_handoff.py` |
| Structured handoff | "Provide the human agent with the request, verified facts, actions taken, supporting evidence, and unresolved questions" | The builder refuses any document that fails the JSON Schema; facts carry their tool and record source; actions carry their verified status. | `test_handoff.py` |
| Documented mock tools | "Sandbox services and mock banking tools are acceptable when their contracts and limitations are documented" | Contracts are exported to `docs/schemas/tools/`; a test fails if they drift. Writes go to `sandbox.*` tables only; the warehouse connection is read-only. | `test_tools.py` |

### Why a cross-customer attempt answers `not_found`

A foreign record and a missing record produce the same response, so the error cannot be used to discover other
customers' ids. The audit log keeps the real reason (`ownership_violation`), the result carries
`handoff_required` with `security_event`, and the session is flagged: from then on the policy engine removes every
write action for that session (rule `SYN-SEC-001`).

### Why the guard checks in this order

Allowlist, session, replay, contract, ownership, policy, confirmation. The confirmation is consumed last so that a
call rejected for another reason does not burn the customer's confirmation. `request_confirmation` runs the same
checks first, so a confirmation is never issued for an action that would be denied.

## Policy

Rules live in `policy/rules.yaml`. Every rule has an id, a `source` (`legal` or `synthetic_policy`) and a
`verification` status. Decisions list each rule that fired with its source, which is what the customer and the
human agent see as the explanation.

| Rule | Country and scope | Window | Source | Verification |
|---|---|---|---|---|
| MX-WINDOW-001 | Mexico, all products | 90 natural days; bank answers in 45 | LTOSF art. 23 | team research |
| CO-WINDOW-001 | Colombia, Web and App | 5 business days; reversal in 15 | Decreto 587 de 2016 | team research |
| CO-WINDOW-002 | Colombia, other channels | 90 natural days | synthetic | pending verification |
| AR-WINDOW-001 | Argentina, credit card | 30 natural days; acknowledge in 7, answer in 15 (60 if foreign) | Ley 25.065 arts. 26-28 | verified in primary text |
| AR-WINDOW-002 | Argentina, other products | 30 natural days; bank resolves in 10 business days | window synthetic; response time BCRA PUSF 3.1.6 | pending verification |

Argentina, what was verified: the text of Ley 25.065 on InfoLEG
(http://servicios.infoleg.gob.ar/infolegInternet/anexos/55000-59999/55556/texact.htm) gives the cardholder 30
days from receiving the statement to question it (art. 26); the issuer must acknowledge within 7 days and answer
within 15, or 60 for charges abroad (art. 27); and the disputed amount is not payable while under review (art.
28). The BCRA "Protección de los Usuarios de Servicios Financieros" ordered text (updated 6 May 2026,
https://www.bcra.gob.ar/archivos/Pdfs/texord/t-pusf.pdf) requires every complaint to be resolved within 10
business days (point 3.1.6). No customer-side claim window for debit cards or accounts was found, so
AR-WINDOW-002 is synthetic and marked `pending_verification`.

Two approximations apply to every window. The dataset has no statement dates, so windows start at the transaction
date, which is never later than the legal anchor; the approximation can only send more cases to a human. Business
days skip weekends but not public holidays.

Decreto 587 covers payment reversal for non-face-to-face sales, so it is applied to the Web and App channels only.

## Tools

| Tool | Kind | Confirmation | Notes |
|---|---|---|---|
| `get_customer_profile` | read | no | Masked: first name and last initial, last 3 of the document, last 4 of product numbers. No credit score or income. Logs never keep any digit of a document number. |
| `list_recent_transactions` | read | no | At most 90 days and 50 rows. |
| `get_transaction` | read | no | Ownership checked. `fraud_score` and `is_fraud` never leave the service; they feed policy only. |
| `find_candidate_charges` | read | no | Ranks own charges against the amount, date and merchant the customer mentioned. |
| `get_dispute_policy` | read | no | Same computation the guard uses to gate writes. |
| `open_dispute_case` | write | yes | Idempotency key; one case per transaction; stored as `pending_human_review` when policy escalates. |
| `block_card` | write | yes | Own active cards only; the source status is overlaid, never modified. |
| `get_case_status` | read | no | Ownership checked; also the verify step for cases. |

### Linking a complaint to a charge

`complaints` has no `transaction_id`, so the disputed charge is inferred. `find_candidate_charges` calls a
`CandidateRanker` (protocol in `tools/ranking.py`); the default `RuleBasedRanker` weighs amount (0.5), date (0.3)
and merchant (0.2) proximity over the hints given. Learned rankers from `ml/` can be passed to `ToolService`
without touching anything else. The ambiguity rule is fixed here so every ranker is judged the same way: a match
is ambiguous when the top score is below 0.60 or beats the second by less than 0.15, and an ambiguous result
never names a best candidate.

On the synthetic fixture (seed 42, 169 dispute complaints whose text could be parsed), the rule-based ranker puts
the linked transaction first in 169 of 169 cases with full hints, and in 162 of 169 with the amount and a vague
date only; the 7 misses were all flagged ambiguous. The fixture text is templated and team-generated, so this is
a check of the logic, not an accuracy estimate for real customers.

## Failure injection

`FaultInjector` covers `warehouse.read`, `case_store.write` and `case_store.read` with the modes `timeout`,
`error`, `stale_read` and `lost_write`. A fault can fire a fixed number of times and heal (the retry recovers) or
fire forever (the fallback hands off). Probabilistic faults use a seeded generator so evaluation runs repeat.

## Audit and retention

`AuditLog` writes one JSONL file per UTC day. Records hold no raw arguments: only a keyed HMAC of them (so a
document number cannot be recovered by hashing candidates) and the masked version. `AUDIT_RETENTION_DAYS` is 3650,
a synthetic value that mirrors the 10 years the BCRA requires for its complaint register (PUSF point 3.1.3);
purging deletes whole expired day files and never edits a file in place.

## Adversarial cases

`eval/cases/security/adversarial.jsonl` holds 24 team-generated cases (13 Spanish, 11 Portuguese) across
cross-customer access, skipping or forging confirmations, policy overrides, data exfiltration and session attacks.
Each case has the injected text, the tool call a fully compromised model would emit, and the expected outcome.
`tests/agent/test_adversarial_cases.py` replays them against the service. This measures the enforcement layer
under the worst assumption; how often a given model falls for the text is a separate evaluation.

## Synthetic policy values

Every value below is a team choice where no approved bank policy was supplied. Each one is labeled
`synthetic_policy` in the rules and in every decision it produces.

| Value | Where | Basis |
|---|---|---|
| Human review at USD 450 or more | SYN-AMOUNT-001 | About the 90th percentile of Purchase amounts in one month of supplied data (2024-03, 121,105 rows) |
| Fraud escalation at `fraud_score` 50 or `is_fraud` true | SYN-FRAUD-001 | In the same month no non-fraud transaction scored above 30.0; 54 of 137 flagged ones scored 50 or more |
| Only Approved and Pending charges are disputable | SYN-STATUS-001 to 003 | Declined moved no money; Reversed was already returned |
| Missing USD amount or required field goes to review | SYN-DATA-001 | Cannot apply the threshold safely |
| Colombia face-to-face window 90 days | CO-WINDOW-002 | No statutory window verified |
| Argentina non-credit window 30 days | AR-WINDOW-002 | Mirrors Ley 25.065; not verified for debit |
| Confirmation for `open_dispute_case` and `block_card` | SYN-CONFIRM-001 | Both write on the customer's behalf |
| Session 15 min, OTP 5 min and 3 attempts, confirmation 5 min | `session.py`, `permissions.py` | Common practice, not a regulation |
| Audit retention 3650 days | `audit.py` | Mirrors BCRA PUSF 3.1.3 |

## Wiring rules for the orchestrator

- Pass `ConfirmationChallenge.token` to the customer's confirm button only. It must never appear in text the model
  reads; a model that sees the token could replay it.
- Call `policy.engine.narrow()` whenever a model proposes actions. The guard already enforces the full decision, so
  narrowing can only make the service stricter.
- Pass `ToolService.customer_names(session)` as `known_names` to `build_handoff` and to any text sent to a model.

## Limitations and deployment work

- Identity, OTP codes, login throttling, revocations, replay ids and consumed confirmations live in process
  memory (expired entries are pruned). Production needs a shared store (for example Redis with TTLs), a real
  identity provider and OTP channel, and throttling per source address as well as per document.
- The sandbox case store is a single DuckDB file; concurrent writers are safe through unique constraints, but a
  multi-process deployment needs the bank's case system or a server database.
- The warehouse is read through the silver tables; gold serving views are still a placeholder.
- A duplicated document number (the fixture has two) never authenticates; the real remedy is a data fix upstream.
- Name masking catches names the service already knows and cued phrases ("me llamo", "meu nome é"); an unknown
  name in free text without a cue is not detected.
- Policy windows need legal review, public holidays per country, and statement dates to anchor correctly.
- Portuguese appears only in team-generated test text; the supplied data has no Portuguese and no Brazil.
- The hash chain detects edits within a process's records; tamper-proof storage (WORM bucket or a ledger table)
  is deployment work.

# Security

Cautela is a hackathon prototype of a banking dispute-intake service. It runs on synthetic and de-identified data,
and the public demo at https://cautela.107-172-6-206.sslip.io uses eight synthetic customers whose login codes are
shown on screen on purpose. Nothing here protects real money or real customers. This document says what the code
defends against, how, where, and what has not been done.

Each control has one of four statuses:

* **Implemented and tested**: in the code, with pytest tests named below.
* **Implemented, not tested**: in the code or the deployment, with no automated test.
* **Planned**: not in the code yet.
* **Out of scope**: deliberately not done for this prototype.

Paths are relative to the repository root. Test names are `file::test`.

## Reporting a vulnerability

Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. The report is visible only to the maintainer. Please do not open a public issue for a vulnerability.

## Threat model

| Actor | What they want | Main controls |
|---|---|---|
| Customer (honest) | Dispute a charge; see only their own data | Session bound to one customer, ownership checks, confirmation before any write, read-back before reporting success |
| Attacker with a stolen document number | Log in as someone else | A document number alone never authenticates; one-time code, attempt limit, per-document challenge cap, per-address rate limit |
| Prompt injector | Make the model reveal another customer's data, skip a confirmation or run a tool | Tools and policy outside the model, deterministic injection detector, policy that only narrows the model's proposal, confirmation token the model never sees |
| Malicious console insider | Read or change more than their role allows; hide what they did | Console key, masked data in the handoff queue, hash-chained audit log. Limited: see "Not done" |
| Compromised LLM provider | Read customer data from prompts; return hostile output | PII masked before any model call, model output treated as a proposal, reply guard, daily spend cap |

Two facts shape everything below. The model never decides: it may propose actions and extract slots, and the
policy engine and the tool layer decide what is allowed. And every customer-facing tool gets the customer from the
validated session, never from its arguments.

## Controls

### Identity and sessions

**One-time code and signed, expiring sessions: implemented and tested.**
`agent/security/session.py` (`IdentityService.start_login`, `verify_otp`, `validate`, `revoke`) and
`agent/security/signing.py` (`Signer`, HMAC-SHA256 with one derived key per purpose, constant-time comparison).
The code has 6 digits, expires after 5 minutes and locks after 3 wrong attempts. A session token expires after 15
minutes. An unknown document gets a challenge of the same shape and no code, so the response does not tell whether
the document exists. The key comes from `SESSION_SECRET` (at least 32 bytes). When it is missing, the API generates a
random per-process secret and logs a warning (`agent/orchestrator/wiring.py`, `session_secret`).
Tests: `tests/agent/test_session.py::test_login_with_second_factor_binds_session_to_one_customer`,
`::test_document_number_alone_never_authenticates`, `::test_unknown_document_gets_same_shaped_challenge_and_no_code`,
`::test_otp_expires`, `::test_otp_locks_after_max_attempts_even_with_right_code`, `::test_expired_session_is_rejected`,
`::test_tampered_token_is_rejected`, `::test_token_signed_with_another_secret_is_rejected`,
`::test_token_minted_for_another_purpose_does_not_verify_as_session`; `tests/api/test_api.py::test_expired_and_revoked_sessions`.

**Demo logins limited to the seeded identities: implemented and tested.** Demo mode (`CAUTELA_DEMO_MODE`) publishes
the seeded identities and shows their one-time codes through `GET /demo/outbox/{challenge_id}`, standing in for the
customer's phone. It is off unless the environment turns it on (`api/settings.py`); the public demo turns it on in
`deploy/docker-compose.behind-proxy.yml`. Even with it on, `Runtime.start_login` (`api/runtime.py`) keeps a readable
code only for customers in the seed file. Any other customer in the loaded warehouse gets a normal challenge whose code
stays in the mock channel, so a document number alone does not log in as them.
Tests: `tests/api/test_audit_and_limits.py::test_demo_outbox_shows_codes_of_seeded_identities_only`,
`::test_demo_mode_is_off_unless_the_environment_turns_it_on`.

**Replay protection: implemented and tested.** A one-time code is used once. A request id is accepted once per
session (`IdentityService.consume_request_id`); a replayed id is denied and flags the session
(`agent/security/permissions.py`, `PermissionGuard.authenticate`). A confirmation token is consumed once
(`ConfirmationService.consume`). A logged-out token is revoked.
Tests: `tests/agent/test_session.py::test_otp_is_single_use`, `::test_request_id_replay_is_rejected`,
`::test_revoked_session_token_replay_is_rejected`; `tests/agent/test_permissions.py::test_replayed_request_is_denied_and_flags_the_session`,
`::test_confirmation_is_single_use`, `::test_logged_out_session_cannot_be_reused`.

**Per-document login throttle: implemented and tested.** `IdentityService._throttled`: at most
`CAUTELA_LOGIN_CHALLENGES` new challenges per document per 15 minutes (code default 3, read in
`agent/orchestrator/wiring.py`). Past the cap a challenge is still returned, with the same shape, and no code is sent.
With the defaults an attacker gets at most 9 guesses per document per 15 minutes against a 6-digit code.
Tests: `tests/agent/test_session.py::test_fresh_challenges_are_throttled_per_document`,
`::test_login_throttle_is_configurable_for_the_public_demo`, `::test_identity_service_rejects_a_zero_challenge_cap`.

**Per-address rate limits: implemented and tested.** `api/ratelimit.py` (in-memory sliding window) applied in
`api/app.py` (`limited`), keyed by the client address (`client_address`, see "Visitors behind the frontend proxy").
`CAUTELA_RATE_AUTH` covers the login endpoints, `CAUTELA_RATE_TURN` the conversation endpoints and
`CAUTELA_RATE_CONSOLE` the read-only console and audit endpoints, as requests per seconds (code defaults `10/60`,
`30/60` and `60/60`, parsed in `api/settings.py`). Tests: `tests/api/test_api.py::test_auth_endpoints_are_rate_limited`,
`::test_turn_endpoint_is_rate_limited`, `::test_settings_parse_and_validate`;
`tests/api/test_audit_and_limits.py::test_console_routes_are_rate_limited`.

**Public demo values.** The deployed demo raises the limits:

| Variable | Public demo | Code default |
|---|---|---|
| `CAUTELA_LOGIN_CHALLENGES` | 30 per document per 15 min | 3 |
| `CAUTELA_RATE_AUTH` | `60/60` | `10/60` |
| `CAUTELA_RATE_TURN` | `120/60` | `30/60` |

The demo identities are shared by every visitor and their codes are shown on screen, so the per-document cap
protects nothing there, and at the default of 3 a few judges trying the same customer would lock each other out for
15 minutes. With a real identity provider the defaults, or stricter values, apply.

**X-Forwarded-For trusted only from the proxy: implemented and tested.** The rate limits key on the client address,
which behind the reverse proxy must come from X-Forwarded-For. `deploy/serve.py` enables proxy headers only for
`CAUTELA_TRUSTED_PROXY` (the Docker gateway `10.83.30.1` in `deploy/docker-compose.behind-proxy.yml`; `127.0.0.1` when
unset). From any other peer the header is ignored, so a caller cannot pick its own rate-limit bucket. `python -m api`
does not read proxy headers at all. Tests: `tests/deploy/test_forwarded_for.py` (all three tests). Not tested: that
OpenLiteSpeed actually sends the header in production; `deploy/README.md` step 5 checks it from the container log.

**Visitors behind the frontend proxy: implemented and tested at the API.** Judges reach the API through the Vercel
route (`app/src/app/api/cautela/[...path]/route.ts`), so X-Forwarded-For names Vercel, and without more every visitor
would share one bucket. With `CAUTELA_PROXY_KEY` set on both sides, the route sends the key in `X-Cautela-Proxy-Key`
and the visitor address in `X-Cautela-Client`. `api/app.py` (`client_address`) uses that address only when the key
matches (constant-time comparison) and the value parses as an IP address; otherwise it uses the socket peer. A
caller's own X-Forwarded-For is never read for this. The route builds its outgoing headers itself, so a browser
cannot set either header, and it takes the visitor address from `x-real-ip`, which Vercel sets and overwrites, only
when it runs on Vercel. Tests: `tests/api/test_audit_and_limits.py::test_forwarded_client_is_trusted_only_with_the_proxy_key`,
`::test_without_a_configured_proxy_key_nothing_forwarded_is_read`, `::test_visitors_behind_the_proxy_get_their_own_buckets`,
`::test_a_short_proxy_key_is_refused`. Not tested: the route itself (no frontend test covers it) and Vercel's header.

### Tools and data access

**Deny by default, no customer_id in tool arguments: implemented and tested.** Every tool call goes through
`ToolService.execute` (`agent/service.py`) and `PermissionGuard` (`agent/security/permissions.py`): tool allowlist,
contract validation that rejects unknown fields such as `customer_id`, ownership, policy, confirmation. The customer
comes from the session. Tests: `tests/agent/test_permissions.py::test_unknown_tool_is_denied_by_default`,
`::test_customer_id_cannot_be_injected_into_tools_without_record_ids`, `::test_reads_without_record_ids_only_return_own_data`;
`tests/api/test_api.py::test_auth_errors_are_typed_and_do_not_echo_input`.

**Another customer's record looks missing and raises a security event: implemented and tested.** The guard answers
`not_found`, the same answer as for a record that does not exist, and the service flags the session
(`cross_customer_access`), writes the real reason to the audit log and hands off with `security_event`
(`agent/service.py`, `_denied`; `agent/orchestrator/actions.py`). A flagged session loses every write for the rest of
the login session. Tests: `tests/agent/test_permissions.py::test_cross_customer_access_is_denied_as_not_found`,
`::test_missing_record_and_foreign_record_look_the_same`, `::test_cross_customer_confirmation_cannot_be_obtained`;
`tests/orchestrator/test_adversarial.py::test_security_flag_blocks_writes_for_the_rest_of_the_session`;
`tests/agent/test_policy.py::test_security_flags_remove_all_writes`;
`tests/api/test_api.py::test_other_customers_cannot_read_a_conversation_or_case`.

**Confirmation token bound to session, tool and arguments, never shown to the model: implemented and tested.**
`ConfirmationService.issue` signs the customer, session, tool, an HMAC of the exact arguments and a 5-minute expiry.
The token lives only in server-side state (`PendingConfirmation.token` in `agent/orchestrator/state.py`, excluded from
`repr`); the customer confirms by a confirmation id. No prompt, reply, trail or API response carries it.
Tests: `tests/agent/test_permissions.py::test_confirmation_is_bound_to_exact_arguments`,
`::test_confirmation_is_bound_to_tool_session_and_time`, `::test_tampered_confirmation_is_rejected`,
`::test_write_without_confirmation_is_denied`;
`tests/orchestrator/test_llm_paths.py::test_confirmation_token_never_reaches_a_prompt_reply_trail_or_audit`;
`tests/api/test_api.py::test_confirmation_token_never_leaves_the_server`.

**Policy only narrows: implemented and tested.** `agent/policy/engine.py`, `narrow`: the model's proposal can remove
allowed actions or add escalation reasons. It cannot add an action, skip a confirmation or cancel an escalation.
Tests: `tests/agent/test_policy.py::test_model_cannot_add_actions_skip_confirmation_or_cancel_escalation`,
`::test_narrowing_never_widens_on_random_proposals`;
`tests/orchestrator/test_decide_and_failures.py::test_narrowed_writes_are_a_subset_of_the_policy_decision`.

**Verify by read-back: implemented and tested.** After a write the service reads the case (`get_case_status`) or
the card status (`get_customer_profile`) back and reports success only if it matches; otherwise it hands off with
`tool_failure` (`agent/orchestrator/actions.py`, `_verify_case`, `_verify_block`).
Tests: `tests/agent/test_tools.py::test_verify_step_catches_writes_that_did_not_persist`,
`::test_block_card_verify_catches_lost_write`;
`tests/orchestrator/test_decide_and_failures.py::test_lost_write_is_caught_by_the_verify_step`.

**Data at rest: classified, stored in clear.** Every column of the data contracts carries a classification
(`pii_direct`, `pii_quasi`, `sensitive_financial`, `none`; `tests/test_classification.py`), and storage does not act
on it yet. The local copy of the delivery (`data/raw/`) and bronze, silver and `quarantine.records` in the warehouse
hold document numbers, names, dates of birth, email, phones and addresses in clear, and the DuckDB files are not
encrypted. The public demo ships the bronze and silver rows of its eight organizer customers, with those fields, to
the VPS inside the demo bundle, where the serving container and anyone with shell access to the host can read them.
Gold serving tables hold the customer's first and last name and no document or contact detail. In production:
keyed-hash tokenization of the document number, an identity store or column masking by role for names and contacts,
encryption at rest with managed keys, a retention limit on quarantined raw records, and synthetic identities in any
demo copy. Details: "Data classification and data at rest" in `data_engineering/README.md`.

### The model

**PII masking before any model call: implemented and tested, with residual risk.** `agent/security/pii.py`
(`mask_text`, `mask_mapping`) masks emails, card and account numbers (last 4 kept), phone numbers (last 2 kept), CURP,
document numbers and known customer names, and names after cues such as "me llamo". The model port accepts only a
sealed `MaskedPrompt` (`agent/llm/port.py`), so an adapter cannot be handed raw text.
Residual risk, stated in the module and tested where it is a known behavior:
* a document number written right after an amount word ("pagué por 12.345.678") is kept, because it reads as an
  amount (`tests/agent/test_pii_audit.py::test_residual_risk_document_written_as_an_amount_is_kept`);
* a person's name that is not the customer's and has no cue before it is not masked;
* street addresses have no pattern;
* merchant names, amounts, dates and record ids are sent on purpose, because the model needs them.
Tests: `tests/agent/test_pii_audit.py::test_mask_text_removes_pii`;
`tests/agent/test_llm.py::test_unmasked_document_never_reaches_the_adapter`,
`::test_adapter_refuses_prompts_not_built_by_the_port`;
`tests/orchestrator/test_llm_paths.py::test_document_number_is_masked_before_the_adapter`.

**Injection detector whose flag the model cannot remove: implemented and tested.**
`agent/orchestrator/injection.py` (`detect`) runs in `agent/orchestrator/routing.py` (`_screen`) before the model is
called and before the disposition. The model may add a flag only with a quote that appears in the message; it cannot
remove the deterministic one. On the 754 validation dispute descriptions it flags none.
Tests: `tests/orchestrator/test_injection.py::test_the_model_cannot_remove_the_deterministic_flag`,
`::test_an_ungrounded_model_flag_is_dropped`, `::test_false_positive_rate_on_validation_dispute_descriptions`.
A detector of this kind can be evaded by wording it has not seen. What limits the damage is that the model cannot
act: tools, policy and confirmation do not depend on it.

**LLM daily spend cap: implemented and tested, in the deployed process only.** `agent/llm/budget.py`
(`BudgetedAdapter`, `DailyBudget`): `LLM_DAILY_MAX_CALLS` (default 2000) and `LLM_DAILY_MAX_USD` (default 1.00) per
UTC day, persisted in `LLM_BUDGET_FILE`. A refused call never reaches the provider and the service falls back to its
deterministic parser and templates. Only `deploy/serve.py` wraps the adapter; `python -m api` and `python -m
agent.demo` are not capped. The USD figure is an estimate from list prices, so set a hard limit on the provider
account too. Tests: `tests/agent/test_llm_budget.py` (for example
`::test_call_cap_refuses_without_contacting_the_provider`, `::test_deployed_app_caps_the_model_and_reports_it_in_health`).

**Reviewer translation bounded: implemented and tested.** `POST /conversations/{id}/translate` (`api/views.py`,
`translate`) accepts only a whole message of the caller's own conversation, so one long message cannot be sent piece
by piece. A translated message is cached and answers again without a model call. A model call counts against
`CAUTELA_TRANSLATE_PER_SESSION` per login session (default 10) and `CAUTELA_RATE_TRANSLATE` across all callers
(default `30/3600`), on top of the per-address turn limit and the daily cap. The call runs outside the lock that
conversation turns wait on. Tests: `tests/api/test_translate.py::test_only_a_whole_message_is_accepted`,
`::test_model_calls_are_capped_per_session_and_cached_answers_are_free`, `::test_model_calls_are_capped_across_all_sessions`,
`::test_the_model_call_runs_outside_the_runtime_lock`.

### Audit

**Hash-chained audit log: implemented and tested.** `agent/security/audit.py` (`AuditLog`): one JSONL file per UTC
day, each record carries the hash of the previous one, arguments are stored masked plus a keyed hash. The chain runs
across day files and restarts: a new process continues from the last stored record. Every record of a conversation
carries its `conversation_id`, whoever wrote it (orchestrator step, tool service, model port), so all turns of one
conversation can be read together (`GET /console/conversations`, `GET /console/conversations/{id}/audit`).
Tests: `tests/agent/test_pii_audit.py::test_audit_log_is_hash_chained_and_detects_edits`,
`::test_audit_record_stores_masked_args_and_keyed_hash_only`;
`tests/agent/test_audit_chain.py::test_records_of_a_bound_trace_carry_the_conversation_id`,
`::test_a_restart_continues_the_stored_chain_instead_of_starting_a_second_one`, `::test_the_chain_spans_day_files`,
`::test_concurrent_writers_keep_one_chain`;
`tests/api/test_audit_and_limits.py::test_every_record_of_every_turn_carries_the_conversation_id`.

**The chain status checks the stored files: implemented and tested.** The "Hash chain intact" status in the API and
on `/audit` comes from `AuditLog.check_stored`, which reads the day files on disk and re-hashes every record, and it
says so (`source: stored_files`, with the number of records and files checked). An edited, deleted or unreadable line
reports `broken` with the first bad sequence number. The result is cached per file by size and modification time, and
the check runs outside the lock conversation turns wait on. A service started without an audit directory reports
`source: memory`, and the page says that only process memory was checked.
Tests: `tests/agent/test_audit_chain.py::test_the_stored_check_reads_the_files_and_catches_an_edit_that_memory_cannot`,
`::test_a_deleted_or_unreadable_line_breaks_the_stored_chain`, `::test_an_unchanged_log_is_not_verified_again`,
`::test_without_a_directory_the_check_says_it_covers_memory_only`;
`tests/api/test_audit_and_limits.py::test_chain_status_reads_the_stored_files_and_reports_an_edit`,
`::test_without_an_audit_directory_the_status_says_it_checked_memory`.

**Retention of audit files: implemented and tested.** `AUDIT_RETENTION_DAYS` = 3650, a synthetic policy constant with
no environment variable. `purge_expired` removes whole expired day files when the log starts (every service start,
each demo reset included) and again on the first record of each new UTC day. After a purge the chain check starts at
the oldest kept record. Tests: `tests/agent/test_pii_audit.py::test_retention_purges_only_whole_expired_day_files`;
`tests/agent/test_audit_chain.py::test_expired_day_files_are_purged_when_the_log_starts`,
`::test_expired_day_files_are_purged_when_the_day_changes`, `::test_after_a_purge_the_check_starts_at_the_oldest_kept_record`;
`tests/api/test_audit_and_limits.py::test_the_service_start_purges_expired_audit_files`.
Retention covers the audit files only. Sessions, conversations, the handoff queue and the sandbox case store live in
process memory and end with the process; nothing retains or purges them on a schedule. Text sent to the model
provider falls under the provider's own retention.

Limits: the record hash is a plain SHA-256, so someone who can write the files can rewrite the whole chain
consistently; the chain detects edits, not a rewrite by the host. The public demo deletes the audit files at every
reset (below), so it keeps at most 30 minutes of trail.

### Console

**Console access: implemented and tested at the API; open and bounded on the public demo, as a demo decision.** The
API's `/console/*` routes require `X-Console-Key` (constant-time comparison, `api/app.py`, `console`); without it they
answer 403 (`tests/api/test_api.py::test_human_case_reaches_the_console_queue_with_audit`). Every console route is a
read. The Next.js proxy (`app/src/app/api/cautela/[...path]/route.ts`) adds the key on the server when
`CAUTELA_CONSOLE_PROXY=enabled`; the key never reaches the browser.

The public demo runs with that flag on so judges can open the agent console and the audit trail without an account.
Anyone who opens the frontend can read the handoff queue and the audit trail of the synthetic customers. What bounds
it: the proxy forwards console requests only as GET, the API rate limits console reads per visitor
(`CAUTELA_RATE_CONSOLE`, keyed on the visitor address the proxy sends), the handoff queue holds masked summaries and
tool-read facts, and audit records hold masked arguments. There are no roles, no per-agent accounts and no record of
which person read what. In production the flag stays off and the console sits behind the bank's single sign-on, with
per-agent accounts, roles, and every console read written to the audit log.

### Deployment

**Container and host hardening: implemented, not tested by pytest.** `deploy/docker-compose.behind-proxy.yml`
publishes the port on `127.0.0.1:8330` only, runs the container read-only as uid 10001 with `cap_drop: ALL`,
`no-new-privileges` and memory, CPU and pid limits. OpenLiteSpeed terminates TLS and sends HSTS
(`max-age=31536000`) and other headers from `deploy/vhost.conf.example`. The API itself sets `nosniff`,
`X-Frame-Options: DENY`, `no-referrer` and `no-store` (`tests/api/test_api.py::test_security_headers_and_demo_mode_off`)
and allows only configured CORS origins (`::test_cors_allows_only_configured_origins`).
`deploy/audit.sh` checks on the server: ports bound to loopback, resource limits, read-only root, `cap_drop`,
`no-new-privileges`, non-root user, no literal credentials, `.env` mode 600, secrets of at least 32 characters, no
`.env` in the image, `/health`, the ACME path not proxied, HSTS present, and the firewall status. Its exit code is
the number of failed checks. HSTS and the compose settings are checked only by this script, which runs on the
server after each deploy, not in CI.

**Demo reset every 30 minutes: implemented, not tested.** In demo mode `deploy/serve.py` ends the process every
`CAUTELA_DEMO_RESET_S` seconds (1800) and Docker's restart policy starts a clean one. Sessions, conversations, the
handoff queue, the sandbox case store, rate-limit counters and the demo outbox are in process memory, so they are
all gone; the audit files are deleted on start. The LLM budget file is kept. See `deploy/README.md`, "Known limits".

**Input limits: implemented and tested.** Messages are capped at 2000 characters (`api/models.py`,
`MAX_MESSAGE_CHARS` in `agent/orchestrator/core.py`); unexpected errors return a typed error without internals
(`tests/api/test_api.py::test_unexpected_errors_do_not_leak_internals`).

## Not done

These are known gaps. Each would have to be closed before real customers used the service.

* **No real identity provider.** The one-time code goes to a mock outbox, and in demo mode the demo logins and their
  codes are published. Authentication here shows the shape of the flow (second factor, binding, expiry, throttles),
  not a real proof of identity. Out of scope for the prototype.
* **Demo console open by design.** See "Console". A real console needs the bank's single sign-on, per-agent
  accounts, roles, and an audit of reads. Planned only in the sense that it is listed here; nothing is built.
* **Conversation turns still hold the process lock during model calls.** Translation calls run outside it; the
  extraction and reply calls of a turn do not, so a slow provider slows every other visitor's turn. Planned: a lock
  per conversation.
* **Product reads still on silver.** The tools read products from `silver.products`. `gold.customer_products` is
  built and `tests/gold/test_gold_products.py` reconciles it with silver, but switching now would change the content
  hash of the frozen eval_fresh warehouse slice. Planned: switch after the final evaluation.
* **No WAF and no request body size limit** at the application or the proxy. Out of scope.
* **All state in memory, reset every 30 minutes.** Rate-limit counters, sessions and the case store live in one
  process; a restart clears them, and there is one process, so the limits do not hold across replicas. A real
  deployment needs a shared store. Planned.
* **Controls without pytest tests:** the 30-minute reset, the container hardening and HSTS (checked by
  `deploy/audit.sh` on the server only), whether the proxy sends X-Forwarded-For in production (checked by
  reading the container log), and the frontend route that sends the visitor address. Trusting X-Forwarded-For only
  from the configured proxy, and the visitor address only with the proxy key, do have tests
  (`tests/deploy/test_forwarded_for.py`, `tests/api/test_audit_and_limits.py`).
* **Audit chain not keyed.** See "Audit". Planned: key the record hash, or ship records to append-only storage the
  host cannot rewrite.
* **Single shared console key** and `SESSION_SECRET` rotation invalidates every session at once. Out of scope.
* **OpenAPI docs are public** (`/docs`, `/openapi.json`). They describe endpoints that are already public.
* **Secrets.** None are in this repository. The server reads them from an env file with mode 600, which
  `deploy/audit.sh` checks.

/**
 * Types that mirror the contracts in docs/schemas exactly.
 *
 * Tool outputs: docs/schemas/tools/*.json (the `output` block of each tool).
 * Handoff: docs/schemas/handoff.schema.json.
 * Envelopes (ToolResult, ConfirmationChallenge, LoginChallenge, AuditRecord) mirror the Python models in
 * agent/tools/contracts.py, agent/security/permissions.py, agent/security/session.py and agent/security/audit.py.
 *
 * The live conversation API types are generated from docs/schemas/openapi.json (`npm run gen:api`) into
 * src/lib/api/generated and re-exported at the end of this file.
 */
import type { components } from "./generated/openapi";

export type Language = "es" | "pt";

/* ---------- tools/get_customer_profile.json ---------- */
export interface ProductSummary {
  product_id: string;
  product_type: string | null;
  product_number_masked: string;
  currency: string | null;
  status: string | null;
}

export interface CustomerProfile {
  customer_ref: string;
  display_name: string;
  document_masked: string;
  email_masked: string | null;
  phone_masked: string | null;
  country: string | null;
  segment: string | null;
  products: ProductSummary[];
}

/* ---------- TransactionView (get_transaction, list_recent_transactions, find_candidate_charges) ---------- */
export interface TransactionView {
  transaction_id: string;
  transaction_date: string | null;
  amount: number | null;
  currency: string | null;
  merchant_name: string | null;
  /** MCC code as delivered (ISO 18245). */
  merchant_category?: string | null;
  /** Food, Transport, Services, Entertainment, Health or Other. */
  transaction_category?: string | null;
  channel: string | null;
  transaction_type: string | null;
  transaction_status: string | null;
  product_id: string | null;
  transaction_country: string | null;
  transaction_city: string | null;
}

/* ---------- tools/find_candidate_charges.json ---------- */
export interface FindCandidateChargesInput {
  amount?: number | null;
  currency?: string | null;
  date_hint?: string | null;
  date_tolerance_days?: number;
  merchant_hint?: string | null;
  window_days?: number;
  top_k?: number;
}

export interface CandidateCharge {
  transaction: TransactionView;
  score: number;
  /** Ranker explanation, e.g. "amount=0.93" (agent/tools/ranking.py RuleBasedRanker.explain). */
  reasons: string[];
}

export interface CandidateChargesResult {
  candidates: CandidateCharge[];
  is_ambiguous: boolean;
  best_transaction_id: string | null;
  ranker: string;
  ambiguity_rule: string;
  searched: number;
}

/* ---------- tools/get_dispute_policy.json ---------- */
export interface RuleHit {
  rule_id: string;
  source: string;
  verification?: string | null;
  message: string;
}

/** Keys set by agent/policy/engine.py; the schema allows any extra key. */
export interface PolicyFacts {
  window_rule?: string;
  window_deadline?: string;
  days_since_transaction?: number;
  amount_usd?: number | null;
  fraud_signal?: boolean;
  /** ASSUMPTION: not produced by the engine yet. Shown only when present. See live.ts TODO list. */
  bank_response_deadline?: string;
  [key: string]: unknown;
}

export interface PolicyDecision {
  policy_version: string;
  allowed_actions: string[];
  required_confirmations: string[];
  must_escalate: boolean;
  escalation_reasons: string[];
  rules_fired: RuleHit[];
  facts?: PolicyFacts;
}

export interface DisputePolicyView {
  transaction_id: string;
  decision: PolicyDecision;
}

/* ---------- tools/open_dispute_case.json + get_case_status.json ---------- */
export interface OpenDisputeCaseInput {
  transaction_id: string;
  idempotency_key: string;
  reason?: "unrecognized_charge";
  customer_statement?: string;
}

export type CaseStatus = "open" | "pending_human_review";

export interface CaseView {
  case_id: string;
  transaction_id: string;
  status: CaseStatus;
  created_at: string;
  policy_rule_ids: string[];
  idempotent_replay?: boolean;
  already_open?: boolean;
}

/* ---------- tools/block_card.json ---------- */
export interface CardBlockView {
  product_id: string;
  block_id: string;
  effective_status: string;
  created_at: string;
}

/* ---------- tool error codes (the union of every tool's `errors` block) ---------- */
export type ToolErrorCode =
  | "validation_error"
  | "session_invalid"
  | "session_expired"
  | "session_revoked"
  | "replay_detected"
  | "request_id_invalid"
  | "tool_not_allowed"
  | "tool_unavailable"
  | "not_found"
  | "policy_denied"
  | "confirmation_required"
  | "confirmation_invalid"
  | "idempotency_conflict"
  | "not_verified";

/** Mirrors agent/tools/contracts.py ToolResult. */
export interface ToolResult<T> {
  trace_id: string;
  tool: string;
  ok: boolean;
  data: T | null;
  error: { code: ToolErrorCode | string; message: string } | null;
  verification: "verified" | "not_verified" | null;
  handoff_required: boolean;
  handoff_reason: string | null;
  rule_ids: string[];
  attempts: number;
}

/** Mirrors agent/security/permissions.py ConfirmationChallenge. */
export interface ConfirmationChallenge {
  confirmation_id: string;
  token: string;
  tool: string;
  summary: Record<string, unknown>;
  expires_at: string;
}

/* ---------- identity (agent/security/session.py) ---------- */
export interface LoginChallenge {
  challenge_id: string;
  channel_hint: string;
  expires_at: string;
}

export interface SessionGrant {
  token: string;
  expires_at: string;
  customer_ref: string;
}

/* ---------- handoff.schema.json ---------- */
export type TransferReasonCode =
  | "policy_requires_review"
  | "amount_above_threshold"
  | "low_confidence"
  | "tool_failure"
  | "suspected_fraud"
  | "customer_requested_human"
  | "out_of_scope"
  | "security_event";

export type ActionStatus = "verified" | "failed" | "not_verified";

export interface Handoff {
  handoff_id: string;
  trace_id: string;
  created_at: string;
  language: Language;
  customer_ref: string;
  request: { summary: string; intent: string; disputed_transaction_ids?: string[] };
  transfer_reason: { code: TransferReasonCode; rule_ids: string[]; confidence?: number };
  verified_facts: { fact: string; source: string }[];
  actions_taken: { action: string; status: ActionStatus; record_id?: string }[];
  evidence?: string[];
  open_questions: string[];
}

/* ---------- audit trail (agent/security/audit.py AuditRecord) ---------- */
export interface AuditRecord {
  trace_id: string;
  seq: number;
  ts: string;
  step: string;
  tool: string | null;
  args_hash: string | null;
  masked_args: Record<string, unknown>;
  rule_ids: string[];
  outcome: string;
  reason: string | null;
  latency_ms: number;
  customer_ref: string | null;
  attempts: number;
  prev_hash: string;
  record_hash: string;
}

/**
 * Trace endpoint (GET /console/traces/{trace_id}). The live chain status covers the whole log and does not say
 * where it breaks, so first_bad_seq is null there; the mock computes it.
 */
export interface TraceView {
  trace_id: string;
  records: AuditRecord[];
  chain: { status: "intact" | "broken"; checked_at: string; first_bad_seq: number | null; records_checked?: number };
}

/* ---------- live conversation API (generated from docs/schemas/openapi.json) ---------- */
export type TurnResponse = components["schemas"]["TurnResponse"];
export type OptionView = components["schemas"]["OptionView"];
export type ConfirmationView = components["schemas"]["ConfirmationView"];
export type RecognitionView = components["schemas"]["RecognitionView"];
/** Verified fields of one charge (tool reads only), on options, the recognition question and the confirmation. */
export type ChargeView = components["schemas"]["ChargeView"];
/** One ranker feature that fired for a charge, with its es/pt label (agent/orchestrator/evidence.py). */
export type MatchReason = components["schemas"]["MatchReason"];
export type MatchCode = MatchReason["code"];
export type ClaimWindow = components["schemas"]["ClaimWindow"];
export type TurnStage = TurnResponse["stage"];

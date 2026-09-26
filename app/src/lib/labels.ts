import type { ActionStatus, TransferReasonCode } from "@/lib/api/types";
import type { StatusKind } from "@/components/ui/status-chip";

export const REASON: Record<TransferReasonCode, { label: string; kind: StatusKind }> = {
  policy_requires_review: { label: "Policy requires review", kind: "stop" },
  amount_above_threshold: { label: "Amount at or above USD 450", kind: "stop" },
  low_confidence: { label: "Charge not identified", kind: "cf" },
  tool_failure: { label: "Tool failure", kind: "bad" },
  suspected_fraud: { label: "Suspected fraud", kind: "bad" },
  customer_requested_human: { label: "Customer asked for a person", kind: "hu" },
  out_of_scope: { label: "Out of scope", kind: "hu" },
  security_event: { label: "Security event", kind: "bad" },
};

export const ACTION_STATUS: Record<ActionStatus, { label: string; kind: StatusKind }> = {
  verified: { label: "Verified", kind: "ok" },
  failed: { label: "Failed", kind: "bad" },
  not_verified: { label: "Not verified", kind: "stop" },
};

/**
 * Rule catalog, copied from agent/policy/rules.yaml (version 2026-09-26.1). Only the source type and a short
 * summary; the yaml stays the source of truth.
 */
export const RULES: Record<string, { source: "legal" | "synthetic_policy"; summary: string }> = {
  "MX-WINDOW-001": { source: "legal", summary: "Mexico, LTOSF art. 23: 90 natural days to object, 45 to respond" },
  "CO-WINDOW-001": { source: "legal", summary: "Colombia, Decreto 587/2016: Web and App, 5 business days, reversal in 15" },
  "CO-WINDOW-002": { source: "synthetic_policy", summary: "Colombia, face-to-face charges: synthetic 90-day window" },
  "AR-WINDOW-001": { source: "legal", summary: "Argentina, Ley 25.065 arts. 26-28: credit cards, 30 days" },
  "AR-WINDOW-002": { source: "synthetic_policy", summary: "Argentina, debit and accounts: synthetic 30-day window" },
  "SYN-STATUS-001": { source: "synthetic_policy", summary: "Only Approved or Pending charges can be disputed" },
  "SYN-STATUS-002": { source: "synthetic_policy", summary: "Declined: no money moved, explain instead" },
  "SYN-STATUS-003": { source: "synthetic_policy", summary: "Reversed: already returned, explain instead" },
  "SYN-AMOUNT-001": { source: "synthetic_policy", summary: "USD 450 or more goes to human review" },
  "SYN-DATA-001": { source: "synthetic_policy", summary: "USD amount not establishable or required field missing: human review" },
  "SYN-FX-001": { source: "synthetic_policy", summary: "No USD amount in the data: fixed official rates of 2026-09-25 (MXN, COP, ARS, BRL)" },
  "SYN-FRAUD-001": { source: "synthetic_policy", summary: "Fraud score 50 or more, or fraud flag: escalate, offer card block" },
  "SYN-CONFIRM-001": { source: "synthetic_policy", summary: "Writes need an explicit confirmation bound to the arguments" },
  "SYN-SCOPE-001": { source: "synthetic_policy", summary: "Only unrecognized-charge intake; never moves money" },
  "SYN-HUMAN-001": { source: "synthetic_policy", summary: "A customer who asks for a person is transferred" },
  "SYN-SEC-001": { source: "synthetic_policy", summary: "Cross-customer access, replay or injection: security event" },
};

export const LANGUAGE_NAME: Record<string, string> = { es: "Spanish", pt: "Portuguese" };

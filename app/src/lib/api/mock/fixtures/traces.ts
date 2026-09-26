/**
 * Seeded audit traces for the synthetic handoffs. Step names follow agent/service.py and agent/llm/port.py
 * ("auth.verify_otp", "guard", "tool", "verify", "confirmation.issue", "llm.<operation>"). The "handoff" step
 * is an assumption about how the service will record the transfer.
 *
 * LLM cost uses agent/llm/prices.yaml: claude-haiku-4-5 at USD 1.00 input and 5.00 output per million tokens.
 */
import type { MockAuditLog } from "../audit";

export const LLM_MODEL = { provider: "anthropic", model: "claude-haiku-4-5", prompt_version: "llm-port-2026-09-25.1" };
const PRICE_IN = 1.0;
const PRICE_OUT = 5.0;

export function llmArgs(input: number, output: number): Record<string, unknown> {
  const cost = Math.round(((input * PRICE_IN + output * PRICE_OUT) / 1_000_000) * 1e6) / 1e6;
  return { ...LLM_MODEL, input_tokens: input, output_tokens: output, cost_usd: cost };
}

export interface StepSpec {
  step: string;
  tool?: string;
  args?: Record<string, unknown> | null;
  rule_ids?: string[];
  outcome: string;
  reason?: string | null;
  latency_ms: number;
  attempts?: number;
}

export function seedTrace(log: MockAuditLog, traceId: string, customerRef: string, startIso: string, steps: StepSpec[]): void {
  let clock = Date.parse(startIso);
  for (const s of steps) {
    clock += Math.round(s.latency_ms) + 40;
    const args = s.args === undefined ? null : s.args;
    log.record({
      trace_id: traceId,
      ts: new Date(clock).toISOString(),
      step: s.step,
      tool: s.tool ?? null,
      args,
      masked_args: args ?? {},
      rule_ids: s.rule_ids ?? [],
      outcome: s.outcome,
      reason: s.reason ?? null,
      latency_ms: s.latency_ms,
      customer_ref: customerRef,
      attempts: s.attempts ?? 1,
    });
  }
}

/** A guard record followed by the tool record, the way ToolService.call writes them. */
export function guarded(tool: string, args: Record<string, unknown>, result: Omit<StepSpec, "step" | "tool" | "args">): StepSpec[] {
  return [
    { step: "guard", tool, args, outcome: "allowed", latency_ms: 2.4 },
    { step: "tool", tool, args, ...result },
  ];
}

export interface SeedCase {
  traceId: string;
  customerRef: string;
  start: string;
  steps: StepSpec[];
}

const login: StepSpec = { step: "auth.verify_otp", outcome: "session_issued", latency_ms: 31.6 };

export const SEEDED_TRACES: SeedCase[] = [
  {
    traceId: "tr_5b2e9c41a7d03f6e", customerRef: "session:SYN-q7Lm2Xc9", start: "2026-09-25T19:36:02Z",
    steps: [
      login,
      { step: "llm.extract_intent", args: llmArgs(1184, 96), outcome: "ok", latency_ms: 812.4 },
      ...guarded("find_candidate_charges", { amount: 10000, currency: "MXN", merchant_hint: "viajes" },
        { outcome: "ok", latency_ms: 41.7 }),
      ...guarded("get_transaction", { transaction_id: "TX00007730" }, { outcome: "ok", latency_ms: 12.9 }),
      ...guarded("get_dispute_policy", { transaction_id: "TX00007730" }, { outcome: "ok", latency_ms: 9.8,
        rule_ids: ["MX-WINDOW-001", "SYN-AMOUNT-001", "SYN-CONFIRM-001"], reason: "amount_above_threshold" }),
      { step: "confirmation.issue", tool: "open_dispute_case", args: { transaction_id: "TX00007730" },
        outcome: "issued", rule_ids: ["SYN-CONFIRM-001"], latency_ms: 3.2 },
      ...guarded("open_dispute_case", { transaction_id: "TX00007730", idempotency_key: "idem_SYN_7730_a1" },
        { outcome: "ok", latency_ms: 57.3, rule_ids: ["MX-WINDOW-001", "SYN-AMOUNT-001", "SYN-CONFIRM-001"] }),
      { step: "verify", tool: "open_dispute_case", args: { read_back: "open_dispute_case" }, outcome: "verified",
        latency_ms: 14.2 },
      { step: "llm.compose_reply", args: llmArgs(902, 141), outcome: "ok", latency_ms: 655.0 },
      { step: "handoff", args: { handoff_id: "ho_3c9a1e7b52d04f18" }, outcome: "created",
        reason: "amount_above_threshold", rule_ids: ["MX-WINDOW-001", "SYN-AMOUNT-001", "SYN-CONFIRM-001"],
        latency_ms: 6.1 },
    ],
  },
  {
    traceId: "tr_c81f07d2e94b6a35", customerRef: "session:SYN-Hk2pQ8wz", start: "2026-09-25T19:12:40Z",
    steps: [
      login,
      { step: "llm.extract_intent", args: llmArgs(1210, 102), outcome: "ok", latency_ms: 790.2 },
      ...guarded("find_candidate_charges", { amount: 85000, currency: "ARS", date_hint: "2026-09-19" },
        { outcome: "ok", latency_ms: 38.5 }),
      ...guarded("get_dispute_policy", { transaction_id: "TX00009911" }, { outcome: "ok", latency_ms: 10.4,
        rule_ids: ["AR-WINDOW-001", "SYN-CONFIRM-001"] }),
      { step: "confirmation.issue", tool: "open_dispute_case", args: { transaction_id: "TX00009911" },
        outcome: "issued", rule_ids: ["SYN-CONFIRM-001"], latency_ms: 2.9 },
      ...guarded("open_dispute_case", { transaction_id: "TX00009911", idempotency_key: "idem_SYN_9911_b4" },
        { outcome: "error", reason: "tool_unavailable", latency_ms: 3012.6, attempts: 3 }),
      { step: "llm.compose_reply", args: llmArgs(948, 133), outcome: "ok", latency_ms: 701.8 },
      { step: "handoff", args: { handoff_id: "ho_91d4be2a7f0c3e58" }, outcome: "created", reason: "tool_failure",
        rule_ids: ["AR-WINDOW-001", "SYN-CONFIRM-001"], latency_ms: 5.4 },
    ],
  },
  {
    traceId: "tr_0e6a3f95b2c7d148", customerRef: "session:SYN-Zt4nR1ve", start: "2026-09-25T18:58:11Z",
    steps: [
      login,
      { step: "llm.extract_intent", args: llmArgs(1302, 118), outcome: "ok", latency_ms: 866.3 },
      ...guarded("find_candidate_charges", { amount: 310000, currency: "COP", date_hint: "2026-09-24" },
        { outcome: "ok", latency_ms: 44.0 }),
      ...guarded("get_dispute_policy", { transaction_id: "TX00005520" }, { outcome: "ok", latency_ms: 11.2,
        rule_ids: ["CO-WINDOW-001", "SYN-FRAUD-001", "SYN-CONFIRM-001"], reason: "suspected_fraud" }),
      { step: "confirmation.issue", tool: "block_card", args: { product_id: "PRD-SYN-CO-12" }, outcome: "issued",
        rule_ids: ["SYN-CONFIRM-001"], latency_ms: 3.0 },
      ...guarded("block_card", { product_id: "PRD-SYN-CO-12", reason: "suspected_fraud" },
        { outcome: "ok", latency_ms: 48.9 }),
      { step: "verify", tool: "block_card", args: { read_back: "block_card" }, outcome: "verified", latency_ms: 12.7 },
      { step: "confirmation.issue", tool: "open_dispute_case", args: { transaction_id: "TX00005520" },
        outcome: "issued", rule_ids: ["SYN-CONFIRM-001"], latency_ms: 2.8 },
      ...guarded("open_dispute_case", { transaction_id: "TX00005520", idempotency_key: "idem_SYN_5520_c2" },
        { outcome: "ok", latency_ms: 61.0, rule_ids: ["CO-WINDOW-001", "SYN-FRAUD-001", "SYN-CONFIRM-001"] }),
      { step: "verify", tool: "open_dispute_case", args: { read_back: "open_dispute_case" }, outcome: "not_verified",
        reason: "read_back_mismatch", latency_ms: 15.3 },
      { step: "llm.compose_reply", args: llmArgs(1011, 150), outcome: "ok", latency_ms: 720.4 },
      { step: "handoff", args: { handoff_id: "ho_a7e2c95d10b83f64" }, outcome: "created", reason: "suspected_fraud",
        rule_ids: ["CO-WINDOW-001", "SYN-FRAUD-001", "SYN-CONFIRM-001"], latency_ms: 5.9 },
    ],
  },
  {
    traceId: "tr_7d3b8e0a41f9c265", customerRef: "session:SYN-Bw9eK3uj", start: "2026-09-25T18:31:27Z",
    steps: [
      login,
      { step: "llm.extract_intent", args: llmArgs(1127, 88), outcome: "ok", latency_ms: 744.9 },
      ...guarded("find_candidate_charges", { amount: 30000, currency: "COP" }, { outcome: "ok", latency_ms: 40.3 }),
      { step: "llm.compose_reply", args: llmArgs(880, 120), outcome: "ok", latency_ms: 612.5 },
      { step: "handoff", args: { handoff_id: "ho_5f08d3c6e2a19b77" }, outcome: "created", reason: "low_confidence",
        latency_ms: 4.8 },
    ],
  },
  {
    traceId: "tr_e2940c6b7a15d83f", customerRef: "session:SYN-Mn5tY7qa", start: "2026-09-25T17:49:03Z",
    steps: [
      login,
      { step: "llm.extract_intent", args: llmArgs(1398, 74), outcome: "ok", latency_ms: 903.1 },
      { step: "guard", tool: "get_transaction", args: { transaction_id: "TX00000077" }, outcome: "denied",
        reason: "not_found", rule_ids: ["SYN-SEC-001"], latency_ms: 2.2 },
      { step: "handoff", args: { handoff_id: "ho_c43b7e91d0f2a586" }, outcome: "created", reason: "security_event",
        rule_ids: ["SYN-SEC-001"], latency_ms: 5.0 },
    ],
  },
  {
    traceId: "tr_4a1c9f6e3b8d0527", customerRef: "session:SYN-Pc8xW2rn", start: "2026-09-25T17:20:45Z",
    steps: [
      login,
      { step: "llm.extract_intent", args: llmArgs(1045, 61), outcome: "ok", latency_ms: 698.7 },
      { step: "handoff", args: { handoff_id: "ho_2e6f9a04b7d1c835" }, outcome: "created",
        reason: "customer_requested_human", rule_ids: ["SYN-HUMAN-001"], latency_ms: 4.4 },
    ],
  },
];

/** A copy of the last trace whose "handoff" record is edited after hashing, to show a broken chain. */
export const TAMPER_TRACE_ID = "tr_ffff00000000bad1";

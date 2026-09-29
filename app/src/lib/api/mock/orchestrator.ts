/**
 * Server-driven conversation for mock mode, mirroring POST /conversations/turn, /recognize and /confirm
 * (agent/orchestrator). The UI only sends turns and answers; every decision happens here, from the fixtures, and
 * every step is written to the audit log with the same step names the live service uses.
 */
import { ApiError } from "../client";
import type { CaseView, Language, MatchReason, OptionView, TransactionView, TransferReasonCode, TurnResponse, TurnStage } from "../types";
import type { MockCustomer } from "./fixtures/customers";
import { chargeOf, matchReasons, reason } from "./evidence";
import { labelText } from "@/lib/format";
import { caseNote, reasonText, reply, reviewNote } from "./replies";
import { hex, intentOf, randomLatency } from "./util";

export interface MockSession {
  token: string;
  customer: MockCustomer;
  ref: string;
  expiresAt: number;
  revoked: boolean;
}

export interface ToolResultSpec {
  outcome: string;
  rule_ids?: string[];
  reason?: string | null;
  attempts?: number;
}

/** What the orchestrator needs from the mock backend: audit writes, the case store and the handoff queue. */
export interface MockBackend {
  record(traceId: string, s: MockSession, step: string, detail: Record<string, unknown>, extra: ToolResultSpec & { latency_ms: number }): void;
  recordTool(traceId: string, s: MockSession, tool: string, args: Record<string, unknown>, result: ToolResultSpec & { latency_ms: number }): void;
  openCase(s: MockSession, transactionId: string, ruleIds: string[]): CaseView;
  createHandoff(s: MockSession, traceId: string, language: Language, reason: TransferReasonCode, ruleIds: string[], tx?: TransactionView,
    caseView?: CaseView, questions?: string[]): string;
}

interface Pending {
  id: string;
  transactionId: string;
  label: string;
  review: boolean;
  reasons: MatchReason[];
}

interface Conversation {
  id: string;
  session: MockSession;
  language: Language;
  stage: TurnStage;
  options: { index: number; transactionId: string }[];
  recognition: Pending | null;
  pending: (Pending & { expiresAt: number }) | null;
  caseRef: TurnResponse["case"];
  /** The case this conversation filed and read back, kept for a person asked for after filing. */
  filed: CaseView | null;
  handoffId: string | null;
  reason: TransferReasonCode | null;
}

const CONFIRMATION_MS = 5 * 60_000;
const ENDED: TurnStage[] = ["resolved", "recognized", "handed_off", "closed"];
const NONE = /\b(ningun[oa]?|nenhum[a]?|none)\b/i;

/** Same format as agent/orchestrator/steps.py tx_label. */
export function txLabel(t: TransactionView): string {
  const when = (t.transaction_date ?? "").slice(0, 10);
  const day = when.length === 10 ? `${when.slice(8, 10)}/${when.slice(5, 7)}/${when.slice(0, 4)}` : "?";
  const amount = t.amount === null ? "?" : `${t.amount.toFixed(2)} ${t.currency ?? ""}`.trim();
  return `${day}, ${t.merchant_name ?? t.transaction_type ?? "?"}, ${amount}`;
}

/** Collects one turn's trail and writes each step to the audit log as it happens. */
class Turn {
  readonly traceId = `tr_${hex(16)}`;
  readonly trail: TurnResponse["trail"] = [];
  private readonly started = performance.now();

  constructor(private readonly backend: MockBackend, private readonly session: MockSession) {}

  step(step: string, outcome: string, detail: Record<string, unknown> = {}, ruleIds: string[] = []): void {
    const latency = randomLatency([0.1, 12]);
    this.trail.push({ trace_id: this.traceId, step, outcome, detail, rule_ids: ruleIds, latency_ms: latency });
    this.backend.record(this.traceId, this.session, `orchestrator.${step}`, detail, { outcome, rule_ids: ruleIds, latency_ms: latency });
  }

  tool(tool: string, args: Record<string, unknown>, result: ToolResultSpec = { outcome: "ok" }): void {
    const latency = result.attempts && result.attempts > 1 ? randomLatency([2900, 3100]) : randomLatency([4, 40]);
    this.backend.recordTool(this.traceId, this.session, tool, args, { ...result, latency_ms: latency });
    const ok = result.outcome === "ok";
    this.trail.push({ trace_id: this.traceId, step: `tool.${tool}`, outcome: ok ? "ok" : "error",
      detail: { tool, ok, attempts: result.attempts ?? 1 }, rule_ids: result.rule_ids ?? [], latency_ms: latency });
  }

  elapsed(): number {
    return Math.round((performance.now() - this.started) * 10) / 10;
  }
}

export class MockOrchestrator {
  private readonly conversations = new Map<string, Conversation>();

  constructor(private readonly backend: MockBackend) {}

  turn(session: MockSession, message: string, conversationId: string | null, language: Language): TurnResponse {
    const conv = conversationId ? this.find(session, conversationId) : this.start(session, language);
    conv.language = language;
    const turn = new Turn(this.backend, session);
    turn.step("gate", "session_valid", { conversation_id: conv.id });
    if (conv.stage === "resolved" && conv.filed && intentOf(message) === "customer_requested_human") return this.personAfterFiling(conv, turn, conv.filed);
    if (ENDED.includes(conv.stage)) return this.respond(conv, turn, "closed", reply("closed", conv.language));

    const intent = intentOf(message);
    turn.step("understand", "deterministic_parser", { intent, fallback_reason: "mock_mode" });
    if (intent === "customer_requested_human") return this.handOff(conv, turn, "customer_requested_human", ["SYN-HUMAN-001"]);
    if (intent === "out_of_scope") return this.handOff(conv, turn, "out_of_scope", ["SYN-SCOPE-001"]);

    if (conv.stage === "awaiting_confirmation" && conv.pending) {
      return this.respond(conv, turn, "pending_confirmation", reply("pending_confirmation", conv.language, { label: conv.pending.label }));
    }
    if (conv.stage === "awaiting_recognition" && conv.recognition) {
      return this.respond(conv, turn, "pending_recognition", reply("pending_recognition", conv.language, { label: conv.recognition.label }));
    }
    if (conv.stage === "clarifying") {
      if (NONE.test(message)) {
        turn.step("decide.selection", "none_of_these");
        return this.handOff(conv, turn, "low_confidence", []);
      }
      const picked = conv.options.find((o) => o.index === Number(message.match(/\d+/)?.[0]));
      if (picked) {
        turn.step("decide.selection", "customer_selected", { option: picked.index, kind: "transaction" });
        const reasons = [...matchReasons(conv.session.customer, picked.transactionId, conv.language), reason("customer_selected", conv.language)];
        return this.propose(conv, turn, picked.transactionId, reasons);
      }
    }
    return this.search(conv, turn);
  }

  recognize(session: MockSession, conversationId: string, recognitionId: string, recognized: boolean): TurnResponse {
    const conv = this.find(session, conversationId);
    const check = conv.recognition;
    if (!check || check.id !== recognitionId) throw new ApiError("not_found", "No pending recognition question with that id");
    const turn = new Turn(this.backend, session);
    turn.step("gate", "session_valid", { conversation_id: conv.id });
    turn.step("recognize.answer", recognized ? "recognized" : "not_recognized", { recognition_id: check.id, transaction_id: check.transactionId });
    conv.recognition = null;
    if (recognized) {
      conv.stage = "recognized";
      return this.respond(conv, turn, "recognized", reply("recognized", conv.language, { label: check.label }));
    }
    conv.pending = { ...check, id: hex(16), expiresAt: Date.now() + CONFIRMATION_MS };
    conv.stage = "awaiting_confirmation";
    turn.step("confirm.request", "issued", { tool: "open_dispute_case", review: check.review }, ["SYN-CONFIRM-001"]);
    const note = check.review ? reviewNote("amount_above_threshold", conv.language) : "";
    return this.respond(conv, turn, "confirm_open", reply("confirm_open", conv.language, { label: check.label, review: note }));
  }

  confirm(session: MockSession, conversationId: string, confirmationId: string, accept: boolean): TurnResponse {
    const conv = this.find(session, conversationId);
    const pending = conv.pending;
    if (!pending || pending.id !== confirmationId || Date.now() > pending.expiresAt) {
      throw new ApiError("not_found", "No pending confirmation with that id");
    }
    const turn = new Turn(this.backend, session);
    turn.step("gate", "session_valid", { conversation_id: conv.id });
    turn.step("confirm.answer", accept ? "accepted" : "declined", { confirmation_id: confirmationId, tool: "open_dispute_case" });
    conv.pending = null;
    if (!accept) {
      conv.stage = "closed";
      return this.respond(conv, turn, "declined", reply("declined", conv.language));
    }
    const customer = session.customer;
    const tx = customer.transactions.find((t) => t.transaction_id === pending.transactionId);
    const rules = customer.policies[pending.transactionId]?.decision.rules_fired.map((r) => r.rule_id) ?? [];
    const args = { transaction_id: pending.transactionId, idempotency_key: `idem_${conv.id}_${pending.transactionId}` };
    if (customer.outcome === "tool_unavailable") {
      turn.tool("open_dispute_case", args, { outcome: "error", reason: "tool_unavailable", attempts: 3, rule_ids: rules });
      return this.handOff(conv, turn, "tool_failure", rules, tx);
    }
    const opened = this.backend.openCase(session, pending.transactionId, rules);
    turn.tool("open_dispute_case", args, { outcome: "ok", rule_ids: rules });
    turn.tool("get_case_status", { case_id: opened.case_id });
    turn.step("verify", "verified", { case_id: opened.case_id, expected_status: opened.status, read_status: opened.status });
    conv.caseRef = { case_id: opened.case_id, verified: true, claim_window: this.claimWindow(conv, pending.transactionId) };
    conv.filed = opened;
    if (opened.status === "pending_human_review") return this.handOff(conv, turn, "amount_above_threshold", rules, tx, opened);
    conv.stage = "resolved";
    return this.respond(conv, turn, "resolved", reply("resolved", conv.language, { label: pending.label, case_id: opened.case_id }));
  }

  private start(session: MockSession, language: Language): Conversation {
    const conv: Conversation = { id: `cv_${hex(16)}`, session, language, stage: "collecting", options: [], recognition: null,
      pending: null, caseRef: null, filed: null, handoffId: null, reason: null };
    this.conversations.set(conv.id, conv);
    return conv;
  }

  /** Another customer's conversation id answers exactly like an unknown one. */
  private find(session: MockSession, id: string): Conversation {
    const conv = this.conversations.get(id);
    if (!conv || conv.session.customer !== session.customer) throw new ApiError("not_found", "Conversation not found");
    return conv;
  }

  private search(conv: Conversation, turn: Turn): TurnResponse {
    const { customer } = conv.session;
    const { hints, candidates, transactions } = customer;
    turn.tool("find_candidate_charges", { ...hints });
    const detail = { ranker: candidates.ranker, pool_size: candidates.searched, ambiguity_rule: candidates.ambiguity_rule };
    const shown = !candidates.is_ambiguous && candidates.best_transaction_id ? [candidates.best_transaction_id] : candidates.ranked.map((r) => r.id);
    turn.step("decide.reasons", "computed", {
      reasons: Object.fromEntries(shown.map((id) => [id, matchReasons(customer, id, conv.language).map((r) => r.code)])),
    });
    if (!candidates.is_ambiguous && candidates.best_transaction_id) {
      turn.step("decide.disposition", "resolve", detail);
      return this.propose(conv, turn, candidates.best_transaction_id, matchReasons(customer, candidates.best_transaction_id, conv.language));
    }
    conv.options = shown.map((id, i) => ({ index: i + 1, transactionId: id }));
    conv.stage = "clarifying";
    turn.step("decide.disposition", "clarify", detail);
    turn.tool("get_customer_profile", {});
    const lines = conv.options.map((o) => {
      const t = transactions.find((x) => x.transaction_id === o.transactionId);
      return `${o.index}) ${t ? labelText(txLabel(t), conv.language) : o.transactionId}`;
    });
    return this.respond(conv, turn, "clarify_options", reply("clarify_options", conv.language, { options: lines.join("\n") }));
  }

  private propose(conv: Conversation, turn: Turn, transactionId: string, reasons: MatchReason[]): TurnResponse {
    const customer = conv.session.customer;
    const tx = customer.transactions.find((t) => t.transaction_id === transactionId);
    const policy = customer.policies[transactionId];
    turn.tool("get_transaction", { transaction_id: transactionId }, { outcome: tx ? "ok" : "error", reason: tx ? null : "not_found" });
    const rules = policy?.decision.rules_fired.map((r) => r.rule_id) ?? [];
    turn.tool("get_dispute_policy", { transaction_id: transactionId }, { outcome: policy ? "ok" : "error", rule_ids: rules });
    if (!tx || !policy?.decision.allowed_actions.includes("open_dispute_case")) {
      return this.handOff(conv, turn, "policy_requires_review", rules, tx);
    }
    conv.options = [];
    conv.recognition = { id: `rc_${hex(16)}`, transactionId, label: txLabel(tx), review: policy.decision.must_escalate, reasons };
    conv.stage = "awaiting_recognition";
    turn.step("recognize.request", "shown", { recognition_id: conv.recognition.id, transaction_id: transactionId, reasons: reasons.map((r) => r.code) });
    return this.respond(conv, turn, "recognize_check", reply("recognize_check", conv.language, { label: conv.recognition.label }));
  }

  /**
   * Like agent/orchestrator/routing.py _person_after_resolution: after the case was filed and read back, a request
   * for a person is a customer_requested_human transfer that carries the filed case, so nothing is filed again.
   */
  private personAfterFiling(conv: Conversation, turn: Turn, filed: CaseView): TurnResponse {
    turn.step("understand", "deterministic_parser", { intent: "customer_requested_human", fallback_reason: "mock_mode" });
    const tx = conv.session.customer.transactions.find((t) => t.transaction_id === filed.transaction_id);
    return this.handOff(conv, turn, "customer_requested_human", ["SYN-HUMAN-001"], tx, filed,
      [`The customer asked for a person after dispute case ${filed.case_id} was filed and verified; continue from it, nothing has to be filed again.`]);
  }

  private handOff(conv: Conversation, turn: Turn, reason: TransferReasonCode, rules: string[], tx?: TransactionView, caseView?: CaseView,
    questions?: string[]): TurnResponse {
    const id = this.backend.createHandoff(conv.session, turn.traceId, conv.language, reason, rules, tx, caseView, questions);
    turn.step("escalate", reason, { handoff_id: id }, rules);
    conv.stage = "handed_off";
    conv.handoffId = id;
    conv.reason = reason;
    conv.pending = null;
    conv.recognition = null;
    const text = reply("handed_off", conv.language, {
      reason: reasonText(reason, conv.language), case: caseView ? caseNote(caseView.case_id, conv.language) : "",
    });
    return this.respond(conv, turn, "handed_off", text);
  }

  /** The claim window the policy engine computed for a charge (window rule id and deadline). */
  private claimWindow(conv: Conversation, transactionId: string): { rule_id: string; deadline: string } | null {
    const facts = conv.session.customer.policies[transactionId]?.decision.facts;
    return facts?.window_rule && facts.window_deadline ? { rule_id: facts.window_rule, deadline: facts.window_deadline } : null;
  }

  private respond(conv: Conversation, turn: Turn, kind: string, text: string): TurnResponse {
    turn.step("reply", "template", { kind, note: "mock_mode" });
    turn.step("turn", conv.stage);
    const customer = conv.session.customer;
    const txOf = (id: string) => customer.transactions.find((x) => x.transaction_id === id);
    const options: OptionView[] = conv.stage === "clarifying" ? conv.options.map((o) => {
      const t = txOf(o.transactionId);
      return { index: o.index, kind: "transaction", label: t ? txLabel(t) : o.transactionId, charge: t ? chargeOf(customer, t) : null,
        reasons: matchReasons(customer, o.transactionId, conv.language) };
    }) : [];
    const r = conv.recognition;
    const rt = r ? txOf(r.transactionId) : undefined;
    const p = conv.pending;
    const pt = p ? txOf(p.transactionId) : undefined;
    return {
      conversation_id: conv.id, trace_id: turn.traceId, language: conv.language, stage: conv.stage, reply: text,
      reply_source: "template", options,
      recognition: r && rt ? { recognition_id: r.id, label: r.label, charge: chargeOf(customer, rt), reasons: r.reasons,
        claim_window: this.claimWindow(conv, r.transactionId) } : null,
      confirmation: p ? { confirmation_id: p.id, tool: "open_dispute_case", label: p.label, expires_at: new Date(p.expiresAt).toISOString(),
        review: p.review, charge: pt ? chargeOf(customer, pt) : null, reasons: p.reasons, claim_window: this.claimWindow(conv, p.transactionId) } : null,
      case: conv.caseRef,
      handoff_id: conv.handoffId, transfer_reason: conv.reason, trail: turn.trail,
      llm: { calls: 0, failed: 0, input_tokens: 0, output_tokens: 0, latency_ms: 0, cost_usd: 0, provider: null, model: null },
      latency_ms: turn.elapsed(),
    };
  }
}

/**
 * Mock implementation of CautelaApi, with the same contract as the live service in api/: login with a one-time
 * code, then conversation turns and confirmations (the orchestration lives in ./orchestrator.ts), plus the
 * console reads. State lives in browser memory and resets on reload; every step writes a real hash-chained audit
 * record so the audit view can show the trace of a case the viewer just ran.
 */
import { ApiError, type CautelaApi, type Translation } from "../client";
import type { CaseView, Handoff, Language, LoginChallenge, SessionGrant, TraceView, TransactionView, TransferReasonCode, TurnResponse } from "../types";
import { money } from "@/lib/format";
import { MockAuditLog } from "./audit";
import { MOCK_CUSTOMERS, TEST_OTP, findCustomerByDocument, type MockCustomer } from "./fixtures/customers";
import { SEEDED_HANDOFFS } from "./fixtures/handoffs";
import { AUTHORED_ENGLISH } from "./fixtures/translations";
import { SEEDED_TRACES, TAMPER_TRACE_ID, guarded, seedTrace } from "./fixtures/traces";
import { MockOrchestrator, type MockBackend, type MockSession, type ToolResultSpec } from "./orchestrator";
import { hex, randomLatency, sleep } from "./util";

export interface MockOptions {
  /** [min, max] simulated latency in ms. Tests pass [0, 0]. */
  latency?: [number, number];
}

export class MockCautelaApi implements CautelaApi, MockBackend {
  readonly mode = "mock" as const;
  private readonly latency: [number, number];
  private readonly audit = new MockAuditLog();
  private readonly challenges = new Map<string, { customer: MockCustomer | null; used: boolean }>();
  private readonly sessions = new Map<string, MockSession>();
  private readonly cases = new Map<string, CaseView>();
  private readonly handoffs: Handoff[] = [...SEEDED_HANDOFFS];
  private readonly orchestrator = new MockOrchestrator(this);

  /** Mock mode runs on the browser clock, so there is no demo date to show. */
  demoClock(): Promise<string | null> {
    return Promise.resolve(null);
  }

  constructor(options: MockOptions = {}) {
    this.latency = options.latency ?? [220, 640];
    for (const seed of SEEDED_TRACES) seedTrace(this.audit, seed.traceId, seed.customerRef, seed.start, seed.steps);
    const last = SEEDED_TRACES[SEEDED_TRACES.length - 1];
    if (last) {
      seedTrace(this.audit, TAMPER_TRACE_ID, last.customerRef, last.start, last.steps);
      this.audit.tamper(TAMPER_TRACE_ID, 2, { outcome: "closed", reason: null });
    }
  }

  private async wait(range: [number, number] = this.latency): Promise<void> {
    await sleep(randomLatency(this.latency[1] === 0 ? [0, 0] : range));
  }

  private session(token: string): MockSession {
    const s = this.sessions.get(token);
    if (!s) throw new ApiError("session_invalid", "Session token missing or unknown");
    if (s.revoked) throw new ApiError("session_revoked", "Session was closed");
    if (Date.now() > s.expiresAt) throw new ApiError("session_expired", "Session token past its expiry");
    return s;
  }

  /* ---------- MockBackend: what the orchestrator writes ---------- */
  record(traceId: string, s: MockSession, step: string, detail: Record<string, unknown>, extra: ToolResultSpec & { latency_ms: number }): void {
    this.audit.record({
      trace_id: traceId, ts: new Date().toISOString(), step, tool: null, args: detail, masked_args: detail,
      rule_ids: extra.rule_ids ?? [], outcome: extra.outcome, reason: extra.reason ?? null, latency_ms: extra.latency_ms,
      customer_ref: s.ref, attempts: extra.attempts ?? 1,
    });
  }

  recordTool(traceId: string, s: MockSession, tool: string, args: Record<string, unknown>, result: ToolResultSpec & { latency_ms: number }): void {
    for (const spec of guarded(tool, args, result)) {
      this.audit.record({
        trace_id: traceId, ts: new Date().toISOString(), step: spec.step, tool: spec.tool ?? null, args: spec.args ?? null,
        masked_args: spec.args ?? {}, rule_ids: spec.rule_ids ?? [], outcome: spec.outcome, reason: spec.reason ?? null,
        latency_ms: spec.latency_ms, customer_ref: s.ref, attempts: spec.attempts ?? 1,
      });
    }
  }

  /** Idempotent per transaction, like the sandbox case store. */
  openCase(s: MockSession, transactionId: string, ruleIds: string[]): CaseView {
    const existing = [...this.cases.values()].find((c) => c.transaction_id === transactionId);
    if (existing) return { ...existing, already_open: true };
    const view: CaseView = {
      case_id: `CASE-${hex(12).toUpperCase()}`, transaction_id: transactionId, created_at: new Date().toISOString(),
      status: s.customer.outcome === "open" ? "open" : "pending_human_review", policy_rule_ids: ruleIds,
    };
    this.cases.set(view.case_id, view);
    return view;
  }

  createHandoff(s: MockSession, traceId: string, language: Language, reason: TransferReasonCode, ruleIds: string[], tx?: TransactionView, caseView?: CaseView): string {
    const id = `ho_${hex(16)}`;
    const rules = ruleIds.length ? [...ruleIds].sort() : reason === "customer_requested_human" ? ["SYN-HUMAN-001"] : [];
    const action: Handoff["actions_taken"] = caseView
      ? [{ action: "open_dispute_case", status: "verified", record_id: caseView.case_id }]
      : reason === "tool_failure" ? [{ action: "open_dispute_case", status: "failed" }] : [];
    this.handoffs.unshift({
      handoff_id: id, trace_id: traceId, created_at: new Date().toISOString(), language, customer_ref: s.ref,
      request: { summary: handoffSummary(language, reason, tx), intent: tx ? "unrecognized_charge" : reason,
        ...(tx ? { disputed_transaction_ids: [tx.transaction_id] } : {}) },
      transfer_reason: { code: reason, rule_ids: rules },
      verified_facts: [
        { fact: "Identity confirmed with a one-time code on the registered channel", source: `auth.verify_otp:${s.ref}` },
        ...(tx ? [{ fact: `Charge exists: ${money(tx.amount, tx.currency, "en")}, ${tx.channel}, status ${tx.transaction_status}`, source: `get_transaction:${tx.transaction_id}` }] : []),
        ...(caseView ? [{ fact: `Case read back with status ${caseView.status}`, source: `get_case_status:${caseView.case_id}` }] : []),
      ],
      actions_taken: action,
      evidence: ["Created in this browser session (mock mode)"],
      open_questions: reason === "tool_failure" ? ["Open the dispute manually; the customer already confirmed"] : [],
    });
    return id;
  }

  /* ---------- auth and demo identities ---------- */
  async listTestIdentities() {
    await this.wait([60, 140]);
    return MOCK_CUSTOMERS.map((c) => c.identity);
  }

  async startLogin(document: string): Promise<LoginChallenge> {
    await this.wait();
    const challengeId = `ch_${hex(12)}`;
    // Unknown documents get the same shape of challenge and no code, as the backend does.
    this.challenges.set(challengeId, { customer: findCustomerByDocument(document) ?? null, used: false });
    return { challenge_id: challengeId, channel_hint: "registered_channel", expires_at: new Date(Date.now() + 5 * 60_000).toISOString() };
  }

  async readTestOutbox(challengeId: string): Promise<string | null> {
    await this.wait([80, 160]);
    return this.challenges.get(challengeId)?.customer ? TEST_OTP : null;
  }

  async verifyOtp(challengeId: string, code: string): Promise<SessionGrant> {
    await this.wait();
    const challenge = this.challenges.get(challengeId);
    if (!challenge || challenge.used) throw new ApiError("otp_invalid", "Unknown or already used challenge");
    if (!challenge.customer || code.replace(/\s/g, "") !== TEST_OTP) throw new ApiError("otp_invalid", "Code does not match");
    challenge.used = true;
    const token = `st_${hex(24)}`;
    const ref = `session:SYN-${hex(8)}`;
    const expiresAt = Date.now() + challenge.customer.sessionSeconds * 1000;
    this.sessions.set(token, { token, customer: challenge.customer, ref, revoked: false, expiresAt });
    return { token, customer_ref: ref, expires_at: new Date(expiresAt).toISOString() };
  }

  async logout(token: string): Promise<void> {
    const s = this.sessions.get(token);
    if (s) s.revoked = true;
  }

  /* ---------- conversation ---------- */
  async turn(token: string, message: string, conversationId: string | null, language: Language): Promise<TurnResponse> {
    await this.wait();
    return this.orchestrator.turn(this.session(token), message, conversationId, language);
  }

  async recognize(token: string, conversationId: string, recognitionId: string, recognized: boolean): Promise<TurnResponse> {
    await this.wait();
    return this.orchestrator.recognize(this.session(token), conversationId, recognitionId, recognized);
  }

  async confirm(token: string, conversationId: string, confirmationId: string, accept: boolean): Promise<TurnResponse> {
    const s = this.session(token);
    // A failing case store retries three times before giving up.
    await this.wait(accept && s.customer.outcome === "tool_unavailable" ? [900, 1300] : this.latency);
    return this.orchestrator.confirm(this.session(token), conversationId, confirmationId, accept);
  }

  /** No model in mock mode: only the fixtures' own English exists; any other text is unavailable, as live without a model. */
  async translate(token: string, conversationId: string, role: "customer" | "assistant", text: string): Promise<Translation> {
    await this.wait([60, 140]);
    this.session(token);
    void conversationId;
    const english = role === "customer" ? AUTHORED_ENGLISH[text.trim()] : undefined;
    if (!english) throw new ApiError("translation_unavailable", "Mock mode has no language model");
    return { text: english, method: "authored", masked: false, provider: null, model: null, cached: false };
  }

  async getCaseStatus(token: string, caseId: string): Promise<CaseView> {
    await this.wait([60, 160]);
    const s = this.session(token);
    const found = this.cases.get(caseId);
    const own = found && s.customer.transactions.some((t) => t.transaction_id === found.transaction_id);
    if (!found || !own) throw new ApiError("not_found", "No such case for this customer");
    return found;
  }

  /* ---------- console ---------- */
  async listHandoffs(): Promise<Handoff[]> {
    await this.wait();
    return [...this.handoffs];
  }

  async getHandoff(handoffId: string): Promise<Handoff> {
    await this.wait();
    const found = this.handoffs.find((h) => h.handoff_id === handoffId);
    if (!found) throw new ApiError("not_found", "No such handoff");
    return found;
  }

  async listTraces() {
    await this.wait();
    return this.audit.traceIds().map((id) => ({
      trace_id: id,
      label: id === TAMPER_TRACE_ID ? "Synthetic tamper test: record 2 edited after hashing"
        : this.handoffs.find((h) => h.trace_id === id)?.transfer_reason.code ?? "Customer session, no handoff",
    }));
  }

  async getTrace(traceId: string): Promise<TraceView> {
    await this.wait();
    const trace = await this.audit.trace(traceId, new Date().toISOString());
    if (!trace) throw new ApiError("not_found", "No such trace");
    return trace;
  }
}

/** The summary is written in the customer's language, as the agent would hand it over. */
const NO_CHARGE: Partial<Record<TransferReasonCode, Record<Language, string>>> = {
  low_confidence: { es: "Cliente no reconoce un cargo; no se identificó cuál.", pt: "Cliente não reconhece uma cobrança; não foi identificada qual." },
  out_of_scope: { es: "Cliente pide algo que este canal no atiende.", pt: "Cliente pede algo que este canal não atende." },
};

function handoffSummary(language: Language, reason: TransferReasonCode, tx: TransactionView | undefined): string {
  if (!tx) {
    return NO_CHARGE[reason]?.[language] ?? (language === "pt" ? "Cliente pede para falar com uma pessoa." : "Cliente pide hablar con una persona.");
  }
  const amount = money(tx.amount, tx.currency, language);
  return language === "pt"
    ? `Cliente não reconhece uma cobrança de ${amount} em ${tx.merchant_name}.`
    : `Cliente no reconoce un cargo de ${amount} en ${tx.merchant_name}.`;
}

/**
 * Live client for the FastAPI service in api/, typed from docs/schemas/openapi.json (`npm run gen:api`).
 *
 * Every call goes to NEXT_PUBLIC_API_BASE_URL, which defaults to "/api/cautela": the Next route handler in
 * src/app/api/cautela/[...path]/route.ts forwards it to CAUTELA_API_URL on the server. That keeps the browser on
 * one origin (no CORS setup) and lets the console key stay server side: the handler adds X-Console-Key to
 * /console/* only, from CAUTELA_CONSOLE_KEY, and only when CAUTELA_CONSOLE_PROXY=enabled.
 *
 * Known limits (documented in README "Frontend"):
 *  - there is no endpoint that lists traces, so /audit lists the traces of queued handoffs;
 *  - the chain status covers the whole log and does not report where it breaks;
 *  - the demo service runs its own clock (it starts the day after the last fixture charge). The session expiry is
 *    taken from `expires_in`; a confirmation expiry is moved onto the browser clock using GET /health
 *    service_clock before the UI schedules anything.
 */
import { ApiError, type ApiErrorCode, type CautelaApi, type TestIdentity, type Translation } from "./client";
import type { components } from "./generated/openapi";
import type { CaseView, Handoff, Language, LoginChallenge, SessionGrant, TraceView, TurnResponse } from "./types";

type Schemas = components["schemas"];

const KNOWN_CODES: ApiErrorCode[] = [
  "session_invalid", "session_expired", "session_revoked", "otp_invalid", "otp_expired", "otp_locked", "rate_limited",
  "tool_unavailable", "not_found", "policy_denied", "confirmation_invalid", "not_verified", "translation_unavailable",
];

function toCode(code: unknown): ApiErrorCode {
  if (code === "conversation_not_found" || code === "no_pending_confirmation" || code === "no_pending_recognition") return "not_found";
  return KNOWN_CODES.includes(code as ApiErrorCode) ? (code as ApiErrorCode) : "unknown";
}

export class LiveCautelaApi implements CautelaApi {
  readonly mode = "live" as const;

  /** Service clock minus browser clock, in ms; read once from GET /health. */
  private offset: Promise<number> | null = null;

  constructor(private readonly baseUrl: string) {}

  private clockOffset(): Promise<number> {
    this.offset ??= this.request<Schemas["HealthResponse"]>("GET", "/health")
      .then((h) => Date.parse(h.service_clock) - Date.now())
      .catch(() => {
        this.offset = null;
        return 0;
      });
    return this.offset;
  }

  /** An instant on the service clock, expressed on the browser clock. */
  private async local(serviceTime: string): Promise<string> {
    const offset = await this.clockOffset();
    return new Date(Date.parse(serviceTime) - offset).toISOString();
  }

  private async localTurn(turn: TurnResponse): Promise<TurnResponse> {
    if (!turn.confirmation) return turn;
    return { ...turn, confirmation: { ...turn.confirmation, expires_at: await this.local(turn.confirmation.expires_at) } };
  }

  private async request<T>(method: "GET" | "POST", path: string, opts: { token?: string; body?: unknown } = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method, headers, cache: "no-store", body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      });
    } catch {
      throw new ApiError("network", "The service could not be reached");
    }
    if (response.status === 204) return undefined as T;
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const error = (payload as Schemas["ErrorResponse"] | null)?.error;
      throw new ApiError(toCode(error?.code), error?.message ?? `HTTP ${response.status}`, error?.trace_id ?? null);
    }
    return payload as T;
  }

  /* ---------- auth and demo identities ---------- */
  async listTestIdentities(): Promise<TestIdentity[]> {
    const list = await this.request<Schemas["DemoIdentity"][]>("GET", "/demo/identities");
    return list.map((i) => ({
      document: i.document_number,
      label: i.label,
      scenario: i.scenario,
      messages: i.messages as Partial<Record<Language, string[]>>,
    }));
  }

  startLogin(document: string): Promise<LoginChallenge> {
    return this.request<Schemas["ChallengeResponse"]>("POST", "/auth/challenge", { body: { document_number: document } });
  }

  async readTestOutbox(challengeId: string): Promise<string | null> {
    try {
      const out = await this.request<Schemas["OutboxResponse"]>("GET", `/demo/outbox/${encodeURIComponent(challengeId)}`);
      return out.code;
    } catch (err) {
      if (err instanceof ApiError && err.code === "not_found") return null;
      throw err;
    }
  }

  async verifyOtp(challengeId: string, code: string): Promise<SessionGrant> {
    const s = await this.request<Schemas["SessionResponse"]>("POST", "/auth/verify", { body: { challenge_id: challengeId, code } });
    // expires_in is relative, so it does not depend on the service and browser clocks agreeing.
    return { token: s.session_token, expires_at: new Date(Date.now() + s.expires_in * 1000).toISOString(), customer_ref: s.customer_ref };
  }

  logout(token: string): Promise<void> {
    return this.request("POST", "/auth/logout", { token });
  }

  /* ---------- conversation ---------- */
  async turn(token: string, message: string, conversationId: string | null, language: Language): Promise<TurnResponse> {
    const turn = await this.request<TurnResponse>("POST", "/conversations/turn", { token, body: { message, conversation_id: conversationId, language } });
    return this.localTurn(turn);
  }

  async recognize(token: string, conversationId: string, recognitionId: string, recognized: boolean): Promise<TurnResponse> {
    const turn = await this.request<TurnResponse>("POST", `/conversations/${encodeURIComponent(conversationId)}/recognize`, {
      token, body: { recognition_id: recognitionId, recognized },
    });
    return this.localTurn(turn);
  }

  async confirm(token: string, conversationId: string, confirmationId: string, accept: boolean): Promise<TurnResponse> {
    const turn = await this.request<TurnResponse>("POST", `/conversations/${encodeURIComponent(conversationId)}/confirm`, {
      token, body: { confirmation_id: confirmationId, accept },
    });
    return this.localTurn(turn);
  }

  getCaseStatus(token: string, caseId: string): Promise<CaseView> {
    return this.request<Schemas["CaseStatusResponse"]>("GET", `/cases/${encodeURIComponent(caseId)}`, { token });
  }

  async translate(token: string, conversationId: string, role: "customer" | "assistant", text: string): Promise<Translation> {
    const t = await this.request<Schemas["TranslationResponse"]>("POST", `/conversations/${encodeURIComponent(conversationId)}/translate`, {
      token, body: { role, text },
    });
    return { text: t.translation, method: "machine", masked: t.masked, provider: t.provider, model: t.model, cached: t.cached };
  }

  /* ---------- console (through the server-side proxy, which adds the console key) ---------- */
  async listHandoffs(): Promise<Handoff[]> {
    const queue = await this.request<Schemas["HandoffQueueItem"][]>("GET", "/console/handoffs");
    return queue.map((item) => toHandoff(item.handoff));
  }

  async getHandoff(handoffId: string): Promise<Handoff> {
    const item = await this.request<Schemas["HandoffQueueItem"]>("GET", `/console/handoffs/${encodeURIComponent(handoffId)}`);
    return toHandoff(item.handoff);
  }

  async listTraces(): Promise<{ trace_id: string; label: string }[]> {
    const handoffs = await this.listHandoffs();
    return handoffs.map((h) => ({ trace_id: h.trace_id, label: h.transfer_reason.code }));
  }

  async getTrace(traceId: string): Promise<TraceView> {
    const t = await this.request<Schemas["TraceView"]>("GET", `/console/traces/${encodeURIComponent(traceId)}`);
    return { ...t, chain: { ...t.chain, first_bad_seq: null } };
  }
}

/** The generated schema allows a null confidence; the handoff contract omits it instead. */
function toHandoff(h: Schemas["Handoff"]): Handoff {
  const { confidence, ...reason } = h.transfer_reason;
  return {
    ...h,
    actions_taken: h.actions_taken.map(({ record_id, ...a }) => (record_id ? { ...a, record_id } : a)),
    transfer_reason: typeof confidence === "number" ? { ...reason, confidence } : reason,
  };
}

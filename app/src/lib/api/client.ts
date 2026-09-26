import type { CaseView, Handoff, Language, LoginChallenge, SessionGrant, TraceView, TurnResponse } from "./types";

/** Codes the UI branches on. Tool codes come from docs/schemas/tools; auth codes from agent/security/session.py. */
export type ApiErrorCode =
  | "session_invalid"
  | "session_expired"
  | "session_revoked"
  | "otp_invalid"
  | "otp_expired"
  | "otp_locked"
  | "rate_limited"
  | "tool_unavailable"
  | "not_found"
  | "policy_denied"
  | "confirmation_invalid"
  | "not_verified"
  | "network"
  | "translation_unavailable"
  | "unknown";

export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly traceId: string | null;
  readonly ruleIds: string[];

  constructor(code: ApiErrorCode, message: string, traceId: string | null = null, ruleIds: string[] = []) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.traceId = traceId;
    this.ruleIds = ruleIds;
  }

  get isSessionEnd(): boolean {
    return this.code === "session_expired" || this.code === "session_revoked" || this.code === "session_invalid";
  }
}

/** A test identity shown on the login screen. Mock mode lists fixtures; live mode reads GET /demo/identities. */
export interface TestIdentity {
  document: string;
  /** Short English description from the source; the customer UI shows its own es/pt text per scenario. */
  label: string;
  /** Mock only: the synthetic person behind the identity, shown under the scenario. */
  person?: string;
  scenario: string;
  /** Example opening messages per language, when the source provides them. */
  messages?: Partial<Record<Language, string[]>>;
}

export interface AuthApi {
  listTestIdentities(): Promise<TestIdentity[]>;
  startLogin(document: string): Promise<LoginChallenge>;
  /** Test channel only: reads the one-time code the mock channel delivered for a challenge. */
  readTestOutbox(challengeId: string): Promise<string | null>;
  verifyOtp(challengeId: string, code: string): Promise<SessionGrant>;
  logout(token: string): Promise<void>;
}

export interface ConsoleApi {
  listHandoffs(): Promise<Handoff[]>;
  getHandoff(handoffId: string): Promise<Handoff>;
  listTraces(): Promise<{ trace_id: string; label: string }[]>;
  getTrace(traceId: string): Promise<TraceView>;
}

/** One message in English, for reviewers. The conversation itself stays in Spanish or Portuguese. */
export interface Translation {
  text: string;
  /** machine: the service's language model (POST /conversations/{id}/translate). authored: a mock fixture's own English. */
  method: "machine" | "authored";
  /** True when personal data was masked before the text reached the model. */
  masked: boolean;
  provider: string | null;
  model: string | null;
  cached: boolean;
}

/**
 * The conversation contract of api/app.py: the service orchestrates, the client sends turns, answers the
 * recognition question and answers confirmations. Mock mode implements the same contract in the browser.
 */
export interface TurnFlowApi {
  turn(token: string, message: string, conversationId: string | null, language: Language): Promise<TurnResponse>;
  /** Answer "do you recognize this charge?": true ends without a dispute, false issues the confirmation. */
  recognize(token: string, conversationId: string, recognitionId: string, recognized: boolean): Promise<TurnResponse>;
  confirm(token: string, conversationId: string, confirmationId: string, accept: boolean): Promise<TurnResponse>;
  getCaseStatus(token: string, caseId: string): Promise<CaseView>;
  /**
   * English version of one message of this conversation (the text must be in its transcript). Live mode calls the
   * service's language model; it fails with translation_unavailable when the service runs without one.
   */
  translate(token: string, conversationId: string, role: "customer" | "assistant", text: string): Promise<Translation>;
}

export interface CautelaApi extends AuthApi, TurnFlowApi, ConsoleApi {
  readonly mode: "mock" | "live";
}

/**
 * Data for the audit pages at conversation grain.
 *
 * Live mode reads two console routes the shared client does not wrap yet: GET /console/conversations (every
 * conversation of the running service, resolved ones included) and GET /console/conversations/{id}/audit (every
 * record of every turn). Both go through the same-origin proxy, which adds the console key server side. Mock mode has
 * no conversation index, so it lists the mock traces through the shared client.
 */
import { ApiError, getApi } from "@/lib/api";
import type { AuditRecord, TraceView } from "@/lib/api/types";

export const LIVE = process.env.NEXT_PUBLIC_API_MODE === "live";
const BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "/api/cautela").replace(/\/$/, "");

/** What the service says its chain check covered. Mock traces are checked in the browser, one trace at a time. */
export type ChainSource = "stored_files" | "memory" | "browser";

export interface Chain {
  status: "intact" | "broken";
  checkedAt: string;
  firstBadSeq: number | null;
  recordsChecked: number;
  filesChecked: number;
  source: ChainSource;
}

export interface ConversationRow {
  /** The conversation id, or the trace id in mock mode, where each trace is one session. */
  id: string;
  isConversation: boolean;
  latestTraceId: string;
  turns: number;
  stage: string | null;
  transferReason: string | null;
  caseId: string | null;
  mockLabel: string | null;
}

export interface AuditScope {
  /** The trace named in the URL. */
  traceId: string;
  conversationId: string | null;
  /** Every turn of the conversation in order; just the trace itself outside a conversation. */
  turnTraceIds: string[];
  records: AuditRecord[];
  chain: Chain;
}

interface ServerChain {
  status: "intact" | "broken";
  checked_at: string;
  records_checked?: number;
  source?: "stored_files" | "memory";
  files_checked?: number;
  first_bad_seq?: number | null;
}

interface ServerConversation {
  conversation_id: string;
  stage: string;
  turns: number;
  trace_ids: string[];
  case_id: string | null;
  transfer_reason: string | null;
}

type LiveTrace = TraceView & { conversation_id?: string | null; conversation_trace_ids?: string[] };

async function consoleGet<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { headers: { Accept: "application/json" }, cache: "no-store" });
  } catch {
    throw new ApiError("network", "The service could not be reached.");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const code = body?.error?.code;
    const known = code === "rate_limited" || code === "not_found" ? code : response.status === 404 ? "not_found" : "unknown";
    throw new ApiError(known, typeof body?.error?.message === "string" ? body.error.message : "Request failed.");
  }
  return body as T;
}

function toChain(c: ServerChain, fallbackRecords: number): Chain {
  return {
    status: c.status,
    checkedAt: c.checked_at,
    firstBadSeq: c.first_bad_seq ?? null,
    recordsChecked: c.records_checked ?? fallbackRecords,
    filesChecked: c.files_checked ?? 0,
    source: c.source ?? "browser",
  };
}

export async function listConversations(): Promise<ConversationRow[]> {
  if (LIVE) {
    const rows = await consoleGet<ServerConversation[]>("/console/conversations");
    return rows.flatMap((r) => {
      const latest = r.trace_ids.at(-1);
      return latest ? [{
        id: r.conversation_id, isConversation: true, latestTraceId: latest, turns: r.turns, stage: r.stage,
        transferReason: r.transfer_reason, caseId: r.case_id, mockLabel: null,
      }] : [];
    });
  }
  const traces = await getApi().listTraces();
  return traces.map((t) => ({
    id: t.trace_id, isConversation: false, latestTraceId: t.trace_id, turns: 1, stage: null, transferReason: null,
    caseId: null, mockLabel: t.label,
  }));
}

/**
 * The trace in the URL, widened to its whole conversation when it belongs to one: the receipt links the last turn,
 * and the charge was chosen in an earlier one. If the conversation read fails, the single trace is still shown.
 */
export async function loadScope(traceId: string): Promise<AuditScope & { partial: boolean }> {
  const trace = (await getApi().getTrace(traceId)) as LiveTrace;
  const own: AuditScope = {
    traceId, conversationId: trace.conversation_id ?? null, turnTraceIds: [traceId], records: trace.records,
    chain: toChain(trace.chain, trace.records.length),
  };
  if (!LIVE || !trace.conversation_id) return { ...own, partial: false };
  try {
    const audit = await consoleGet<{ trace_ids: string[]; records: AuditRecord[]; chain: ServerChain }>(
      `/console/conversations/${encodeURIComponent(trace.conversation_id)}/audit`);
    return {
      traceId, conversationId: trace.conversation_id, turnTraceIds: audit.trace_ids, records: audit.records,
      chain: toChain(audit.chain, audit.records.length), partial: false,
    };
  } catch {
    return { ...own, turnTraceIds: trace.conversation_trace_ids?.length ? trace.conversation_trace_ids : [traceId], partial: true };
  }
}

import type { AuditRecord } from "@/lib/api/types";
import type { StatusKind } from "@/components/ui/status-chip";

export type Phase = "session" | "understand" | "decide" | "act" | "verify" | "escalate";

export const PHASES: { id: Phase; label: string; detail: string }[] = [
  { id: "session", label: "Session", detail: "identity and one-time code" },
  { id: "understand", label: "Understand", detail: "LLM extraction and read tools" },
  { id: "decide", label: "Decide", detail: "permission guard, policy, confirmation" },
  { id: "act", label: "Act", detail: "write tools" },
  { id: "verify", label: "Verify", detail: "read back after each write" },
  { id: "escalate", label: "Escalate", detail: "structured handoff" },
];

const READ_TOOLS = new Set(["get_transaction", "find_candidate_charges", "list_recent_transactions", "get_customer_profile"]);

function toolPhase(tool: string | null | undefined): Phase {
  if (tool === "get_case_status") return "verify";
  if (tool === "get_dispute_policy") return "decide";
  return tool && READ_TOOLS.has(tool) ? "understand" : "act";
}

/** Orchestrator rows from the live service (tool is null): the step name after "orchestrator.". */
function orchestratorPhase(step: string): Phase {
  if (step === "gate") return "session";
  if (step === "understand") return "understand";
  if (step.startsWith("tool.")) return toolPhase(step.slice("tool.".length));
  if (step === "verify") return "verify";
  if (step === "escalate") return "escalate";
  // guard, decide.*, confirm.*, reply, turn
  return "decide";
}

/**
 * Step names from two sources. Mock traces (agent/service.py and agent/llm/port.py): "auth.*", "llm.*", "tool",
 * "verify", "handoff"; "handoff" and "policy" are assumptions (see live-api.ts). Live traces (GET
 * /console/traces/{id}): "guard", "tool" and "verify" with a tool, plus "orchestrator.*" rows with tool null.
 */
export function phaseOf(r: AuditRecord): Phase {
  if (r.step.startsWith("orchestrator.")) return orchestratorPhase(r.step.slice("orchestrator.".length));
  if (r.step.startsWith("auth.")) return "session";
  if (r.step.startsWith("llm.")) return "understand";
  if (r.step === "handoff") return "escalate";
  if (r.step === "verify" || r.tool === "get_case_status") return "verify";
  if (r.step === "tool") return toolPhase(r.tool);
  return "decide";
}

export function outcomeKind(outcome: string): StatusKind | null {
  switch (outcome) {
    case "verified":
    case "allowed":
      return "ok";
    case "handed_off":
    case "not_verified":
      return "stop";
    case "error":
    case "denied":
      return "bad";
    case "issued":
    case "accepted":
      return "cf";
    case "created":
    case "transfer":
      return "hu";
    default:
      return null;
  }
}

const OUTCOME_LABEL: Record<string, string> = {
  ok: "OK",
  verified: "Verified",
  not_verified: "Not verified",
  session_issued: "Session issued",
  handed_off: "Handed off",
  pending_human_review: "Pending human review",
  tool_unavailable: "Tool unavailable",
};

/** Readable outcome label; unknown codes are shown with underscores as spaces and a leading capital. */
export function outcomeLabel(outcome: string): string {
  const known = OUTCOME_LABEL[outcome];
  if (known) return known;
  const words = outcome.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Latency for the audit trail: one decimal under 100 ms ("9.8 ms", "57.0 ms"), whole numbers above. */
export function latency(value: number): string {
  const digits = value < 100 ? 1 : 0;
  return `${new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)} ms`;
}

export interface TraceSummary {
  records: number;
  toolCalls: number;
  verified: number;
  notVerified: number;
  latencyMs: number;
  spanMs: number;
  llmCalls: number;
  llmCostUsd: number | null;
  tokensIn: number;
  tokensOut: number;
  rules: string[];
  start: number;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function summarize(records: AuditRecord[]): TraceSummary {
  const llm = records.filter((r) => r.step.startsWith("llm."));
  const costs = llm.map((r) => num(r.masked_args.cost_usd));
  const starts = records.map((r) => Date.parse(r.ts) - r.latency_ms);
  const ends = records.map((r) => Date.parse(r.ts));
  const start = starts.length ? Math.min(...starts) : 0;
  return {
    records: records.length,
    toolCalls: records.filter((r) => r.step === "tool").length,
    verified: records.filter((r) => r.step === "verify" && r.outcome === "verified").length,
    notVerified: records.filter((r) => r.step === "verify" && r.outcome !== "verified").length,
    latencyMs: records.reduce((sum, r) => sum + r.latency_ms, 0),
    spanMs: ends.length ? Math.max(...ends) - start : 0,
    llmCalls: llm.length,
    llmCostUsd: costs.some((c) => c === null) ? null : costs.reduce<number>((s, c) => s + (c ?? 0), 0),
    tokensIn: llm.reduce((s, r) => s + (num(r.masked_args.input_tokens) ?? 0), 0),
    tokensOut: llm.reduce((s, r) => s + (num(r.masked_args.output_tokens) ?? 0), 0),
    rules: [...new Set(records.flatMap((r) => r.rule_ids))].sort(),
    start,
  };
}

/** Bar position for the waterfall, as fractions of the trace span. */
export function barOf(r: AuditRecord, summary: TraceSummary): { offset: number; width: number } {
  if (summary.spanMs <= 0) return { offset: 0, width: 0 };
  const begin = Date.parse(r.ts) - r.latency_ms - summary.start;
  return { offset: Math.max(0, begin / summary.spanMs), width: Math.max(0.004, r.latency_ms / summary.spanMs) };
}

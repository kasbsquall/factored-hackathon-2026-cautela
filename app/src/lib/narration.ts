/**
 * Plain-English narration of one turn, for reviewers, built only from the structured fields the service returned:
 * the turn trail (step, outcome, detail, rule ids), stage, options, recognition, confirmation, case, handoff and
 * LLM usage. Nothing is inferred: a field the turn does not carry is not mentioned, and a step this module does not
 * know is shown with its raw step name and outcome. Rule summaries come from the catalog in src/lib/labels.ts.
 *
 * Trail vocabulary: agent/orchestrator/core.py, steps.py and actions.py (`_step` calls).
 */
import type { ClaimWindow, MatchReason, TurnResponse } from "@/lib/api/types";
import { dateOnly, money } from "@/lib/format";
import { reasonPhrase } from "@/lib/i18n/match-reasons";
import { REASON, RULES } from "@/lib/labels";

type Step = TurnResponse["trail"][number];

/** What the customer did to produce this turn. */
export type TurnCause =
  | { kind: "message" }
  | { kind: "option"; index: number }
  | { kind: "none" }
  | { kind: "recognize"; recognized: boolean }
  | { kind: "confirm"; accept: boolean };

export type NarrationIcon =
  | "gate" | "read" | "tool" | "decide" | "choose" | "policy" | "ask" | "confirm" | "write" | "verify" | "handoff"
  | "security" | "reply" | "error" | "other";
export type NarrationTone = "ok" | "info" | "stop" | "bad" | "human" | "quiet";

export interface RuleNote {
  id: string;
  /** "law" or "synthetic policy", from the rule catalog; null when the rule is not in the catalog. */
  source: string | null;
  summary: string | null;
  /** Claim deadline the policy engine computed, when the turn carries one for this rule. */
  deadline: string | null;
}

export interface NarrationStep {
  icon: NarrationIcon;
  tone: NarrationTone;
  text: string;
  rules: RuleNote[];
}

export interface TurnNarration {
  traceId: string;
  cause: string;
  stage: string;
  steps: NarrationStep[];
  /** e.g. "34 ms" */
  serviceTime: string;
  /** e.g. "No language model call" */
  llm: string;
}

const WRITE_TOOLS = new Set(["open_dispute_case", "block_card"]);
const INTENT: Record<string, string> = {
  dispute_charge: "dispute a charge",
  unrecognized_charge: "dispute an unrecognized charge",
  select_option: "pick one of the listed options",
  reject_options: "none of the listed options",
  request_human: "talk to a person",
  out_of_scope: "a request outside dispute intake",
  block_card: "block a card",
};
const STAGE: Record<string, string> = {
  collecting: "collecting details",
  clarifying: "asking the customer to choose or add detail",
  awaiting_recognition: "waiting for the customer to say whether they recognize the charge",
  awaiting_confirmation: "waiting for the customer's confirmation",
  resolved: "resolved, case open",
  recognized: "closed, charge recognized",
  handed_off: "handed off to a person",
  abstained: "closed, nothing to dispute",
  closed: "closed",
};
const REJECTION: Record<string, string> = {
  empty: "it was empty",
  too_long: "it was too long",
  number_not_in_facts: "it had a number that is not in the facts",
  missing_required_mention: "it left out a required mention",
  wrong_language: "it was in the wrong language",
};
const SOURCE: Record<string, string> = { legal: "law", synthetic_policy: "synthetic policy" };
/** The disposition step's pool: list_transactions with window_days=90 (agent/orchestrator/core.py POOL_ARGS). */
const POOL = "from the last 90 days";
/** The mock's scripted pools are not a 90-day query, so the window is named only for the live service ("turn" is mock-only). */
const poolText = (turn: TurnResponse) => (turn.trail.some((s) => s.step === "turn") ? "" : ` ${POOL}`);

/** Why the parser read the message instead of the model (agent/orchestrator/intent.py validation_reason). */
function fallbackText(reason: string): string {
  if (reason === "no_llm_configured") return "no language model configured";
  if (reason === "mock_mode") return "mock mode";
  if (reason === "LLMUnavailable") return "the language model was unavailable";
  if (reason === "LLMOutputError" || reason === "SchemaError" || reason === "ValueError") return "the model's output could not be used";
  if (reason.startsWith("llm_invalid_output:")) return `the model's output failed validation on ${reason.slice(19)}`;
  return `fallback: ${reason}`;
}

/** Model proposals the policy engine refused (agent/policy/engine.py narrow). */
function rejectionText(item: string): string {
  const [kind, what = ""] = item.split(/:(.*)/s);
  if (kind === "add_action") return `the policy removed ${what}`;
  if (kind === "skip_confirmation") return `the policy kept the confirmation for ${what}`;
  if (kind === "unknown_reason") return `the policy ignored the unknown reason ${code(what)}`;
  return item;
}

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const list = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const code = (v: string) => `"${v}"`;
/** "Amount at or above USD 450" becomes "amount at or above USD 450": only the first letter changes. */
const lowerFirst = (v: string) => v.charAt(0).toLowerCase() + v.slice(1);

function joinAnd(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function percent(p: number): string {
  // Rounding 0.9997 to "100.0%" would claim certainty the model did not give.
  if (p >= 0.9995 && p < 1) return "above 99.9%";
  return `${(p * 100).toFixed(1)}%`;
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${new Intl.NumberFormat("en-US").format(n)} ${n === 1 ? one : many}`;
}

function claimWindows(turn: TurnResponse): ClaimWindow[] {
  return [turn.recognition?.claim_window, turn.confirmation?.claim_window, turn.case?.claim_window]
    .filter((w): w is ClaimWindow => Boolean(w));
}

function ruleNotes(ids: string[], turn: TurnResponse): RuleNote[] {
  const windows = claimWindows(turn);
  return ids.map((id) => {
    const rule = RULES[id];
    const window = windows.find((w) => w.rule_id === id);
    return {
      id,
      source: rule ? SOURCE[rule.source] ?? rule.source : null,
      summary: rule?.summary ?? null,
      deadline: window ? dateOnly(window.deadline, "en") : null,
    };
  });
}

function attemptsNote(detail: Step["detail"]): string {
  const attempts = num(detail.attempts);
  return attempts !== null && attempts > 1 ? ` (${attempts} attempts)` : "";
}

function understand(step: Step): NarrationStep {
  const d = step.detail;
  if (step.outcome === "injection_detector") {
    return { icon: "security", tone: "bad", text: `The injection detector matched the message (marker ${code(str(d.marker) ?? "?")}); nothing else was read from it.`, rules: [] };
  }
  const reason = str(d.fallback_reason);
  if (reason === "option_pick") {
    const n = num(d.selected_option);
    return { icon: "read", tone: "quiet", text: n !== null ? `Read the reply as a pick of option ${n}.` : "Read the reply as an option pick.", rules: [] };
  }
  const intent = str(d.intent);
  const topic = str(d.topic);
  const cues: string[] = [];
  const amount = num(d.amount);
  if (amount !== null) cues.push(`amount ${money(amount, str(d.currency), "en") || amount}`);
  const date = str(d.date);
  if (date) cues.push(`date ${dateOnly(date, "en")}`);
  const merchant = str(d.merchant);
  if (merchant) cues.push(`merchant ${code(merchant)}`);
  const ref = str(d.record_ref);
  if (ref) cues.push(`reference ${ref}`);
  // parser_escalation_over_llm: the model read the message, then the parser's escalation signal replaced its intent
  // (agent/orchestrator/core.py understand).
  const overridden = reason?.startsWith("parser_escalation_over_llm:") ? reason.slice(27) : null;
  const who = step.outcome === "llm"
    ? "Read the message with the language model (its output is schema-validated)"
    : overridden !== null
      ? `The language model read the message, and the parser's escalation signal overrode its intent (${INTENT[overridden] ?? overridden})`
      : `Read the message with the deterministic parser${reason ? ` (${fallbackText(reason)})` : ""}`;
  const parts = [
    `${who}.`,
    intent ? `Intent: ${INTENT[intent] ?? intent}${topic ? ` (${topic})` : ""}.` : "",
    cues.length ? `Cues: ${cues.join(", ")}.` : intent === "dispute_charge" ? "No amount, exact date or merchant extracted." : "",
    step.outcome === "llm" && reason?.startsWith("dropped_unstated:") ? `Dropped fields the message did not state: ${reason.slice(17)}.` : "",
  ];
  return { icon: "read", tone: "info", text: parts.filter(Boolean).join(" "), rules: [] };
}

function disposition(step: Step, turn: TurnResponse): NarrationStep {
  const d = step.detail;
  const pool = num(d.pool_size);
  const among = pool !== null ? ` among ${plural(pool, "charge")}${poolText(turn)}` : "";
  const cues = list(d.cues);
  const using = cues.length ? ` Cues used: ${joinAnd(cues)}.` : "";
  // Without trained artifacts the service runs the fixed rules (RuleDisposition, name "rules_fixed_baseline"): no
  // probabilities, and its confidence is a rule score.
  const who = (str(d.model) ?? "").startsWith("rules_") ? "The rule baseline (no trained model loaded)" : "The disposition model";
  if (step.outcome === "resolve") {
    const probs = d.probabilities as Record<string, unknown> | undefined;
    const p = num(probs?.match);
    const score = num(d.confidence);
    const how = p !== null ? ` (match probability ${percent(p)})` : score !== null ? ` (top rule score ${score.toFixed(2)})` : "";
    return { icon: "decide", tone: "ok", text: `${who} found one charge that fits${among}${how}.${using}`, rules: [] };
  }
  if (step.outcome === "clarify") {
    const n = turn.options.length;
    const found = n
      ? `Found ${n} candidate charges that fit the description${among}. None was preselected; the customer must choose.`
      : `More than one charge fits the description${among}.`;
    return { icon: "choose", tone: "info", text: `${found}${using}`, rules: [] };
  }
  return { icon: "decide", tone: "stop", text: `${who} found no single charge that fits${among}.${using}`, rules: [] };
}

function reasons(step: Step, turn: TurnResponse): NarrationStep {
  if (step.outcome !== "computed") {
    return { icon: "decide", tone: "stop", text: `Match reasons could not be computed (${str(step.detail.error) ?? step.outcome}); none are shown.`, rules: [] };
  }
  const byTx = (step.detail.reasons ?? {}) as Record<string, unknown>;
  const ids = Object.keys(byTx);
  if (ids.length === 1) {
    const shown: MatchReason[] | undefined = turn.recognition?.reasons ?? turn.confirmation?.reasons;
    const phrases = shown?.length
      ? shown.map((r) => reasonPhrase(r.code, r.value))
      : list(byTx[ids[0] ?? ""]).map((c) => reasonPhrase(c));
    return { icon: "decide", tone: "info", text: phrases.length ? `Why it matched, from the matching features that fired: ${joinAnd(phrases)}.` : "No matching feature fired for this charge.", rules: [] };
  }
  return { icon: "decide", tone: "info", text: `Match reasons computed for each of the ${ids.length} candidates from the matching features that fired; each option shows its own.`, rules: [] };
}

function policy(step: Step, turn: TurnResponse): NarrationStep {
  const d = step.detail;
  const version = str(d.policy_version);
  const writes = list(d.allowed_writes);
  const why = list(d.reasons).map((r) => lowerFirst(REASON[r as keyof typeof REASON]?.label ?? r));
  const rejected = list(d.rejected_proposals);
  const head = `Policy engine${version ? ` (rules version ${version})` : ""}:`;
  let text: string;
  if (writes.includes("open_dispute_case") && step.outcome === "escalate") text = `${head} a dispute may be filed, and a person must review it: ${joinAnd(why)}.`;
  else if (writes.includes("open_dispute_case")) text = `${head} a dispute may be filed.`;
  else if (step.outcome === "escalate") text = `${head} this goes to a person${why.length ? `: ${joinAnd(why)}` : ""}.`;
  else text = `${head} no write is allowed for this charge.`;
  if (rejected.length) {
    const refused = joinAnd(rejected.map(rejectionText));
    text += ` ${refused.charAt(0).toUpperCase()}${refused.slice(1)}.`;
  }
  return { icon: "policy", tone: step.outcome === "escalate" ? "stop" : writes.length ? "ok" : "stop", text, rules: ruleNotes(step.rule_ids, turn) };
}

function tool(step: Step, run: Step[]): NarrationStep {
  const name = step.step.slice(5);
  const d = step.detail;
  if (WRITE_TOOLS.has(name)) {
    if (step.outcome !== "ok") {
      return { icon: "write", tone: "bad", text: `The write ${name} failed (${str(d.error) ?? "error"})${attemptsNote(d)}.`, rules: [] };
    }
    const what = name === "open_dispute_case" ? "Wrote the dispute case" : "Blocked the card";
    return { icon: "write", tone: "ok", text: `${what} with ${name}, using the customer's confirmation${attemptsNote(d)}.`, rules: [] };
  }
  if (step.outcome !== "ok") {
    return { icon: "tool", tone: "bad", text: `Tool ${name} failed (${str(d.error) ?? "error"})${attemptsNote(d)}.`, rules: [] };
  }
  const names = run.map((s) => `${s.step.slice(5)}${attemptsNote(s.detail)}`);
  return { icon: "tool", tone: "quiet", text: `Read with ${names.length === 1 ? "tool" : "tools"} ${joinAnd(names)}.`, rules: [] };
}

function verify(step: Step): NarrationStep {
  const d = step.detail;
  const caseId = str(d.case_id);
  if (caseId) {
    if (step.outcome === "verified") {
      return { icon: "verify", tone: "ok", text: `Case ${caseId} written, then read back from the case store: status ${code(str(d.read_status) ?? "?")} matches.`, rules: [] };
    }
    return { icon: "verify", tone: "bad", text: `Case ${caseId} read back did not match: expected ${code(str(d.expected_status) ?? "?")}, read ${str(d.read_status) ? code(str(d.read_status) as string) : "nothing"}.`, rules: [] };
  }
  const read = str(d.read_status);
  return step.outcome === "verified"
    ? { icon: "verify", tone: "ok", text: `Card read back from the customer profile: status ${code(read ?? "?")}.`, rules: [] }
    : { icon: "verify", tone: "bad", text: `Card block could not be read back (status ${read ? code(read) : "unknown"}).`, rules: [] };
}

function reply(step: Step): NarrationStep {
  const kind = str(step.detail.kind);
  const note = str(step.detail.note);
  const named = kind ? ` ${code(kind)}` : "";
  if (step.outcome === "llm") {
    return { icon: "reply", tone: "quiet", text: "Reply worded by the language model from the verified facts; it passed the grounding check (no number outside the facts, required mentions present, right language).", rules: [] };
  }
  let why = "";
  if (note === "no_llm_configured") why = " (no language model configured)";
  else if (note === "mock_mode") why = " (mock mode)";
  else if (note?.startsWith("llm_failed:")) why = ` (the model call failed: ${note.slice(11)})`;
  else if (note?.startsWith("llm_reply_rejected:")) why = ` (the model's wording was rejected: ${REJECTION[note.slice(19)] ?? note.slice(19)})`;
  else if (note) why = ` (${note})`;
  return { icon: "reply", tone: "quiet", text: `Reply from the fixed template${named}${why}.`, rules: [] };
}

function wroteNothing(trail: Step[]): boolean {
  return !trail.some((s) => s.step.startsWith("tool.") && WRITE_TOOLS.has(s.step.slice(5)) && s.outcome === "ok");
}

function one(step: Step, turn: TurnResponse, run: Step[]): NarrationStep | null {
  const d = step.detail;
  switch (step.step) {
    case "gate":
      return { icon: "gate", tone: "quiet", text: step.outcome === "session_valid" ? "Session checked: valid." : `Session gate: ${step.outcome}.`, rules: [] };
    case "understand":
      return understand(step);
    case "decide.disposition":
      return disposition(step, turn);
    case "decide.reasons":
      return reasons(step, turn);
    case "decide.selection": {
      if (step.outcome === "none_of_these") return { icon: "choose", tone: "info", text: "The customer answered none of these.", rules: [] };
      const n = num(d.option);
      return { icon: "choose", tone: "info", text: `The customer picked option ${n ?? "?"}${str(d.kind) === "card" ? " (a card)" : ""}.`, rules: [] };
    }
    case "decide.status_check":
      return { icon: "decide", tone: "stop", text: `The charge that fits every cue has status ${code(str(d.status) ?? "?")}${num(d.pool_size) !== null ? `, checked among ${plural(num(d.pool_size) as number, "charge")}${poolText(turn)}` : ""}.`, rules: [] };
    case "decide.policy":
      return policy(step, turn);
    case "recognize.request": {
      const fields = list(d.fields).length;
      return { icon: "ask", tone: "info", text: `Showed the charge as the tools read it${fields ? ` (${plural(fields, "field")})` : ""} and asked whether the customer recognizes it.`, rules: [] };
    }
    case "recognize.answer":
      return step.outcome === "recognized"
        ? { icon: "ask", tone: "ok", text: `The customer recognizes the charge.${wroteNothing(turn.trail) ? " No write ran in this turn." : ""}`, rules: [] }
        : { icon: "ask", tone: "info", text: "The customer does not recognize the charge.", rules: [] };
    case "confirm.request": {
      const toolName = str(d.tool) ?? "the action";
      if (step.outcome !== "issued") return { icon: "confirm", tone: "bad", text: `A confirmation for ${toolName} could not be issued (${str(d.error) ?? step.outcome}).`, rules: ruleNotes(step.rule_ids, turn) };
      const review = d.review === true ? " Policy sends the case to human review once it is filed." : "";
      const none = wroteNothing(turn.trail) ? " No write ran in this turn." : "";
      return { icon: "confirm", tone: "info", text: `Asked for an explicit confirmation before running ${toolName}.${none}${review}`, rules: ruleNotes(step.rule_ids, turn) };
    }
    case "confirm.answer":
      return step.outcome === "accepted"
        ? { icon: "confirm", tone: "ok", text: "The customer confirmed.", rules: [] }
        : { icon: "confirm", tone: "info", text: `The customer cancelled.${wroteNothing(turn.trail) ? " No write ran in this turn." : ""}`, rules: [] };
    case "verify":
      return verify(step);
    case "escalate": {
      const reason = REASON[step.outcome as keyof typeof REASON]?.label ?? step.outcome;
      const facts = num(d.facts);
      const actions = num(d.actions);
      const carry = facts !== null && actions !== null ? ` The handoff file carries ${plural(facts, "verified fact")} and ${plural(actions, "action")}.` : "";
      return { icon: "handoff", tone: "human", text: `Handed off to a person: ${lowerFirst(reason)}${str(d.handoff_id) ? ` (handoff ${str(d.handoff_id)})` : ""}.${carry}`, rules: ruleNotes(step.rule_ids, turn) };
    }
    case "security":
      return { icon: "security", tone: "bad", text: `Security flag raised: ${step.outcome.replace(/_/g, " ")}.`, rules: ruleNotes(step.rule_ids, turn) };
    case "reply":
      return reply(step);
    case "error":
      return { icon: "error", tone: "bad", text: `Internal error (${step.outcome}); the turn ends in a handoff.`, rules: [] };
    case "turn":
      return null; // mock-mode bookkeeping, the stage is shown in the header
    default:
      if (step.step.startsWith("tool.")) return tool(step, run);
      return { icon: "other", tone: "quiet", text: `${step.step}: ${step.outcome}.`, rules: ruleNotes(step.rule_ids, turn) };
  }
}

export function causeText(cause: TurnCause): string {
  switch (cause.kind) {
    case "message": return "Customer sent a message";
    case "option": return `Customer picked option ${cause.index}`;
    case "none": return "Customer answered none of these";
    case "recognize": return cause.recognized ? "Customer recognizes the charge" : "Customer does not recognize the charge";
    case "confirm": return cause.accept ? "Customer confirmed" : "Customer cancelled";
  }
}

function llmText(turn: TurnResponse): string {
  const u = turn.llm;
  if (!u || !u.calls) return "No language model call";
  const tokens = (u.input_tokens ?? 0) + (u.output_tokens ?? 0);
  const cost = u.cost_usd === null || u.cost_usd === undefined ? "cost unknown" : `USD ${u.cost_usd.toFixed(4)}`;
  const failed = u.failed ? `, ${u.failed} failed` : "";
  return `Language model: ${plural(u.calls, "call")}${failed}, ${plural(tokens, "token")}, ${cost}${u.model ? ` (${u.model})` : ""}`;
}

/** Narrate one turn. Consecutive successful read tools are folded into one line. */
export function narrateTurn(turn: TurnResponse, cause: TurnCause): TurnNarration {
  const steps: NarrationStep[] = [];
  const trail = turn.trail;
  for (let i = 0; i < trail.length; i += 1) {
    const step = trail[i] as Step;
    const isRead = step.step.startsWith("tool.") && !WRITE_TOOLS.has(step.step.slice(5)) && step.outcome === "ok";
    let run: Step[] = [step];
    if (isRead) {
      while (i + 1 < trail.length) {
        const next = trail[i + 1] as Step;
        if (!(next.step.startsWith("tool.") && !WRITE_TOOLS.has(next.step.slice(5)) && next.outcome === "ok")) break;
        run = [...run, next];
        i += 1;
      }
    }
    const line = one(step, turn, run);
    if (line) steps.push(line);
  }
  return {
    traceId: turn.trace_id,
    cause: causeText(cause),
    stage: turn.stage === "resolved" ? (turn.case ? "resolved, case open" : "resolved, card blocked") : STAGE[turn.stage] ?? turn.stage,
    steps,
    serviceTime: `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(turn.latency_ms)} ms`,
    llm: llmText(turn),
  };
}

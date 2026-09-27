import { narrateTurn } from "@/lib/narration";
import type { TurnResponse } from "@/lib/api/types";
import captured from "../fixtures/live-turns.json";

const runs = captured as unknown as Record<string, TurnResponse[]>;
const texts = (turn: TurnResponse, cause: Parameters<typeof narrateTurn>[1] = { kind: "message" }) =>
  narrateTurn(turn, cause).steps.map((s) => s.text);

describe("narrateTurn on turns captured from the live service", () => {
  it("explains a direct match, the policy with its rules and the recognition question", () => {
    const [first] = runs.normal!;
    const n = narrateTurn(first!, { kind: "message" });
    expect(n.cause).toBe("Customer sent a message");
    expect(n.stage).toBe("waiting for the customer to say whether they recognize the charge");
    const lines = n.steps.map((s) => s.text);
    expect(lines).toContain("Session checked: valid.");
    expect(lines.some((l) => l.startsWith("Read the message with the deterministic parser (no language model configured). Intent: dispute a charge. Cues: amount MXN"))).toBe(true);
    expect(lines).toContain("The disposition model found one charge that fits among 50 charges from the last 90 days (match probability 99.8%). Cues used: amount, date and merchant.");
    expect(lines).toContain("Why it matched, from the matching features that fired: same amount, same day, merchant named and the only charge of the last 90 days that fits every cue.");
    const policy = n.steps.find((s) => s.icon === "policy")!;
    expect(policy.text).toBe("Policy engine (rules version 2026-09-26.2): a dispute may be filed.");
    expect(policy.rules.map((r) => r.id)).toEqual(["MX-WINDOW-001", "SYN-CONFIRM-001"]);
    expect(policy.rules[0]).toMatchObject({ source: "law", deadline: "Aug 28, 2026" });
    expect(policy.rules[0]!.summary).toContain("90 calendar days");
    expect(lines.some((l) => l.startsWith("Showed the charge as the tools read it (") && l.endsWith("fields) and asked whether the customer recognizes it."))).toBe(true);
    expect(lines).toContain('Reply from the fixed template "recognize_check" (no language model configured).');
    expect(n.llm).toBe("No language model call");
    expect(n.serviceTime).toMatch(/^\d[\d,]* ms$/);
  });

  it("folds consecutive read tools into one line and keeps the attempt count", () => {
    const lines = texts(runs.normal![0]!);
    expect(lines).toContain("Read with tools get_transaction and get_dispute_policy.");
    expect(lines).toContain("Read with tool get_customer_profile (2 attempts).");
  });

  it("says the confirmation came first and that nothing was written in that turn", () => {
    expect(texts(runs.normal![1]!, { kind: "recognize", recognized: false })).toEqual([
      "The customer does not recognize the charge.",
      "Asked for an explicit confirmation before running open_dispute_case. No write ran in this turn.",
      'Reply from the fixed template "confirm_open" (no language model configured).',
    ]);
  });

  it("reports the write and the read-back from the case store", () => {
    const lines = texts(runs.normal![2]!, { kind: "confirm", accept: true });
    expect(lines).toContain("The customer confirmed.");
    expect(lines).toContain("Wrote the dispute case with open_dispute_case, using the customer's confirmation (2 attempts).");
    expect(lines.some((l) => /^Case CASE-[0-9A-F]{12} written, then read back from the case store: status "open" matches\.$/.test(l))).toBe(true);
  });

  it("names the review reason and the handoff for a high amount", () => {
    const first = texts(runs.human![0]!);
    expect(first).toContain("Policy engine (rules version 2026-09-26.2): a dispute may be filed, and a person must review it: amount at or above USD 450.");
    const last = narrateTurn(runs.human![2]!, { kind: "confirm", accept: true });
    const handoff = last.steps.find((s) => s.icon === "handoff")!;
    expect(handoff.text).toMatch(/^Handed off to a person: amount at or above USD 450 \(handoff ho_[0-9a-f]{16}\)\. The handoff file carries 4 verified facts and 1 action\.$/);
    expect(handoff.rules.map((r) => r.id)).toContain("SYN-AMOUNT-001");
  });

  it("explains several candidates without preselecting one", () => {
    const lines = texts(runs.ambiguous![0]!);
    expect(lines).toContain("Found 3 candidate charges that fit the description among 28 charges from the last 90 days. None was preselected; the customer must choose. Cues used: date and type.");
    expect(lines).toContain("Match reasons computed for each of the 3 candidates from the matching features that fired; each option shows its own.");
    const pick = texts(runs.ambiguous![1]!, { kind: "option", index: 1 });
    expect(pick).toContain("Read the reply as a pick of option 1.");
    expect(pick).toContain("The customer picked option 1.");
  });

  it("explains a declined charge, a request for a person, an out-of-scope request and an injection", () => {
    const declined = texts(runs.declined![0]!);
    expect(declined).toContain('The charge that fits every cue has status "Declined", checked among 40 charges from the last 90 days.');
    expect(declined).toContain("Policy engine (rules version 2026-09-26.2): no write is allowed for this charge. The policy removed open_dispute_case.");
    expect(texts(runs.human_request![0]!)).toContain("Handed off to a person: customer asked for a person (handoff " + runs.human_request![0]!.handoff_id + "). The handoff file carries 0 verified facts and 0 actions.");
    expect(texts(runs.out_of_scope![0]!)).toContain("Read the message with the deterministic parser (no language model configured). Intent: a request outside dispute intake (credit_limit_increase).");
    const injection = narrateTurn(runs.injection![0]!, { kind: "message" }).steps;
    expect(injection[1]!.text).toBe('The injection detector matched the message (marker "Ignora tus instrucc"); nothing else was read from it.');
    expect(injection[2]).toMatchObject({ text: "Security flag raised: prompt injection.", rules: [expect.objectContaining({ id: "SYN-SEC-001" })] });
  });

  it("never drops a step: every trail entry except mock bookkeeping maps to a line or a folded tool", () => {
    for (const run of Object.values(runs)) {
      for (const turn of run) {
        const steps = narrateTurn(turn, { kind: "message" }).steps;
        const folded = turn.trail.filter((s, i, all) => s.step.startsWith("tool.") && s.outcome === "ok" && !["tool.open_dispute_case", "tool.block_card"].includes(s.step)
          && i > 0 && all[i - 1]!.step.startsWith("tool.") && all[i - 1]!.outcome === "ok" && !["tool.open_dispute_case", "tool.block_card"].includes(all[i - 1]!.step)).length;
        expect(steps.length).toBe(turn.trail.filter((s) => s.step !== "turn").length - folded);
        for (const s of steps) expect(s.text).not.toMatch(/undefined|null|NaN|\[object/);
      }
    }
  });

  it("reports model usage, the grounding check and a rejected model reply from the fields alone", () => {
    const base = runs.normal![1]!;
    const turn: TurnResponse = {
      ...base,
      reply_source: "llm",
      llm: { calls: 2, failed: 0, input_tokens: 900, output_tokens: 100, cost_usd: 0.0012, latency_ms: 800, model: "gpt-x", provider: "openai" },
      trail: [
        { ...base.trail[0]!, step: "understand", outcome: "llm", detail: { intent: "dispute_charge", fallback_reason: "dropped_unstated:merchant", amount: 12.5, currency: "USD" } },
        { ...base.trail[0]!, step: "reply", outcome: "template", detail: { kind: "confirm_open", note: "llm_reply_rejected:number_not_in_facts" } },
        { ...base.trail[0]!, step: "reply", outcome: "llm", detail: { kind: "confirm_open", note: null } },
      ],
    };
    const n = narrateTurn(turn, { kind: "message" });
    expect(n.steps[0]!.text).toBe("Read the message with the language model (its output is schema-validated). Intent: dispute a charge. Cues: amount USD 12.50. Dropped fields the message did not state: merchant.");
    expect(n.steps[1]!.text).toBe('Reply from the fixed template "confirm_open" (the model\'s wording was rejected: it had a number that is not in the facts).');
    expect(n.steps[2]!.text).toMatch(/^Reply worded by the language model from the verified facts; it passed the grounding check/);
    expect(n.llm).toBe("Language model: 2 calls, 1,000 tokens, USD 0.0012 (gpt-x)");
  });

  it("names the rule baseline, parser fallbacks and overrides, and the resolved outcome for what they are", () => {
    const base = runs.normal![0]!;
    const at = base.trail[0]!;
    const turn: TurnResponse = {
      ...base,
      trail: [
        { ...at, step: "understand", outcome: "deterministic_parser", detail: { intent: "dispute_charge", fallback_reason: "LLMUnavailable" } },
        { ...at, step: "understand", outcome: "deterministic_parser", detail: { intent: "dispute_charge", fallback_reason: "llm_invalid_output:amount" } },
        { ...at, step: "understand", outcome: "deterministic_parser", detail: { intent: "request_human", fallback_reason: "parser_escalation_over_llm:dispute_charge" } },
        { ...at, step: "decide.disposition", outcome: "resolve", detail: { model: "rules_fixed_baseline", confidence: 0.8, cues: ["amount"], pool_size: 12, probabilities: null } },
        { ...at, step: "decide.disposition", outcome: "clarify", detail: { model: "rules_fixed_baseline", cues: [], pool_size: 12 } },
      ],
      options: [],
    };
    const lines = texts(turn);
    expect(lines[0]).toBe("Read the message with the deterministic parser (the language model was unavailable). Intent: dispute a charge. No amount, exact date or merchant extracted.");
    expect(lines[1]).toContain("(the model's output failed validation on amount)");
    expect(lines[2]).toBe("The language model read the message, and the parser's escalation signal overrode its intent (dispute a charge). Intent: talk to a person.");
    expect(lines[3]).toBe("The rule baseline (no trained model loaded) found one charge that fits among 12 charges from the last 90 days (top rule score 0.80). Cues used: amount.");
    expect(lines[4]).toBe("More than one charge fits the description among 12 charges from the last 90 days.");
    expect(narrateTurn({ ...base, stage: "resolved", case: null }, { kind: "message" }).stage).toBe("resolved, card blocked");
    expect(narrateTurn(runs.normal![2]!, { kind: "confirm", accept: true }).stage).toBe("resolved, case open");
  });
});

describe("a request after the handoff", () => {
  const base = runs.normal![0]!;
  const withStep = (outcome: string): TurnResponse => ({
    ...base,
    trail: [{ step: "handoff.follow_up", outcome, rule_ids: [],
      detail: { handoff_id: "ho_0123456789abcdef", transfer_reason: "amount_above_threshold", open_questions: 2 } }],
  } as unknown as TurnResponse);

  it("says what was added to the handoff and that the transfer reason stays", () => {
    expect(texts(withStep("block_card"))).toEqual([
      "After the handoff the customer asked to block a card; no card was blocked in this conversation. Added to handoff ho_0123456789abcdef as an open question (2 open questions now); the transfer reason stays amount at or above USD 450.",
    ]);
  });

  it("never prints the raw step name", () => {
    for (const outcome of ["customer_requested_human", "out_of_scope", "new_charge", "not_added", "something_new"]) {
      expect(texts(withStep(outcome)).join(" ")).not.toMatch(/handoff\.follow_up/);
    }
  });
});

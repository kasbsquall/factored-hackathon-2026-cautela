import type { AuditRecord } from "@/lib/api/types";
import { latency, outcomeKind, outcomeLabel, phaseOf } from "@/lib/audit";

const rec = (step: string, tool: string | null = null): AuditRecord => ({
  trace_id: "tr_test", seq: 1, ts: "2026-09-25T10:00:00Z", step, tool, args_hash: null, masked_args: {}, rule_ids: [],
  outcome: "ok", reason: null, latency_ms: 1, customer_ref: null, attempts: 1, prev_hash: "0", record_hash: "1",
});

describe("audit phases", () => {
  it("keeps the mock step mapping", () => {
    expect(phaseOf(rec("auth.verify_otp"))).toBe("session");
    expect(phaseOf(rec("llm.extract_intent"))).toBe("understand");
    expect(phaseOf(rec("tool", "get_transaction"))).toBe("understand");
    expect(phaseOf(rec("tool", "get_dispute_policy"))).toBe("decide");
    expect(phaseOf(rec("tool", "open_dispute_case"))).toBe("act");
    expect(phaseOf(rec("verify", "open_dispute_case"))).toBe("verify");
    expect(phaseOf(rec("handoff"))).toBe("escalate");
    expect(phaseOf(rec("guard", "get_transaction"))).toBe("decide");
  });

  it("maps the live orchestrator steps", () => {
    expect(phaseOf(rec("orchestrator.gate"))).toBe("session");
    expect(phaseOf(rec("orchestrator.understand"))).toBe("understand");
    expect(phaseOf(rec("orchestrator.tool.find_candidate_charges"))).toBe("understand");
    expect(phaseOf(rec("orchestrator.tool.open_dispute_case"))).toBe("act");
    expect(phaseOf(rec("orchestrator.tool.get_case_status"))).toBe("verify");
    expect(phaseOf(rec("orchestrator.decide.disposition"))).toBe("decide");
    expect(phaseOf(rec("orchestrator.confirm.answer"))).toBe("decide");
    expect(phaseOf(rec("orchestrator.verify"))).toBe("verify");
    expect(phaseOf(rec("orchestrator.escalate"))).toBe("escalate");
    expect(phaseOf(rec("orchestrator.reply"))).toBe("decide");
    expect(phaseOf(rec("orchestrator.turn"))).toBe("decide");
  });
});

describe("audit outcomes and latency", () => {
  it("classifies outcomes and gives readable labels", () => {
    expect(outcomeKind("allowed")).toBe("ok");
    expect(outcomeKind("accepted")).toBe("cf");
    expect(outcomeKind("handed_off")).toBe("stop");
    expect(outcomeKind("template")).toBeNull();
    expect(outcomeLabel("not_verified")).toBe("Not verified");
    expect(outcomeLabel("session_valid")).toBe("Session valid");
  });

  it("shows one decimal under 100 ms and whole numbers above", () => {
    expect(latency(9.8)).toBe("9.8 ms");
    expect(latency(57)).toBe("57.0 ms");
    expect(latency(1631.4)).toBe("1,631 ms");
  });
});

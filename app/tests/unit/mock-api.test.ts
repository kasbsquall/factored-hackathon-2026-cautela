import { ApiError } from "@/lib/api/client";
import { MockCautelaApi } from "@/lib/api/mock/mock-api";
import { TAMPER_TRACE_ID } from "@/lib/api/mock/fixtures/traces";

async function login(api: MockCautelaApi, document: string) {
  const challenge = await api.startLogin(document);
  const code = await api.readTestOutbox(challenge.challenge_id);
  return api.verifyOtp(challenge.challenge_id, code ?? "");
}

describe("MockCautelaApi, same contract as POST /conversations/turn", () => {
  it("asks which charge when several match, and never picks one itself", async () => {
    const api = new MockCautelaApi({ latency: [0, 0] });
    const { token } = await login(api, "1020000001");
    const turn = await api.turn(token, "No reconozco un cargo de unos 50 mil pesos", null, "es");
    expect(turn.stage).toBe("clarifying");
    expect(turn.options.map((o) => o.index)).toEqual([1, 2, 3]);
    expect(turn.confirmation).toBeNull();
    expect(turn.case).toBeNull();
    expect(turn.options[1]?.label).toBe("22/09/2026, MERCANUBE*APP SYN, 48900.00 COP");
  });

  it("gives every option its charge data and the reasons that fired, like the live service", async () => {
    const api = new MockCautelaApi({ latency: [0, 0] });
    const { token } = await login(api, "1020000001");
    const turn = await api.turn(token, "No reconozco un cargo de unos 50 mil pesos", null, "es");
    const second = turn.options[1]!;
    expect(second.charge).toMatchObject({ merchant_name: "MERCANUBE*APP SYN", amount: 48900, currency: "COP", channel: "App",
      city: "Bogotá", card_type: "Debit Card", card_last4: "4821", category: "Other", merchant_category: "5399" });
    expect(second.reasons.map((r) => r.code)).toEqual(["amount_close", "date_in_range"]);
    expect(second.reasons[0]!.label).toBe("Monto a 2% del que indicaste");
    expect(turn.options.flatMap((o) => o.reasons.map((r) => r.code))).not.toContain("only_fit");
  });

  it("asks whether the customer recognizes the charge before any confirmation", async () => {
    const api = new MockCautelaApi({ latency: [0, 0] });
    const { token } = await login(api, "1020000002");
    const asked = await api.turn(token, "Tengo un cargo de casi 10 mil pesos de viajes", null, "pt");
    expect(asked.stage).toBe("awaiting_recognition");
    expect(asked.confirmation).toBeNull();
    expect(asked.recognition?.charge.merchant_name).toBe("VIAJES PACIFICO ONLINE SYN");
    expect(asked.recognition?.reasons.map((r) => r.code)).toEqual(["amount_close", "merchant_named", "only_fit"]);
    expect(asked.recognition?.claim_window).toEqual({ rule_id: "MX-WINDOW-001", deadline: "2026-12-17" });
    const done = await api.recognize(token, asked.conversation_id, asked.recognition!.recognition_id, true);
    expect(done).toMatchObject({ stage: "recognized", confirmation: null, case: null, handoff_id: null });
    expect(done.reply).toMatch(/^Obrigado por conferir/);
    await expect(api.recognize(token, asked.conversation_id, asked.recognition!.recognition_id, false)).rejects.toMatchObject({ code: "not_found" });
  });

  it("opens a case only after the customer confirms, and reports it verified", async () => {
    const api = new MockCautelaApi({ latency: [0, 0] });
    const { token } = await login(api, "1020000001");
    const first = await api.turn(token, "No reconozco un cargo", null, "es");
    const asked = await api.turn(token, "2", first.conversation_id, "es");
    expect(asked.stage).toBe("awaiting_recognition");
    expect(asked.recognition?.reasons.at(-1)?.code).toBe("customer_selected");
    const picked = await api.recognize(token, asked.conversation_id, asked.recognition!.recognition_id, false);
    expect(picked.stage).toBe("awaiting_confirmation");
    expect(picked.case).toBeNull();
    expect(picked.confirmation?.charge?.merchant_name).toBe("MERCANUBE*APP SYN");
    const done = await api.confirm(token, picked.conversation_id, picked.confirmation!.confirmation_id, true);
    expect(done.stage).toBe("resolved");
    expect(done.case?.verified).toBe(true);
    const readBack = await api.getCaseStatus(token, done.case!.case_id);
    expect(readBack).toMatchObject({ status: "open", transaction_id: "TX00004182" });
    expect(done.case?.claim_window).toEqual({ rule_id: "CO-WINDOW-001", deadline: "2026-09-29" });
  });

  it("hands off with the filed case when a person is asked for after filing, like tests/orchestrator/test_person_after_filing.py", async () => {
    const api = new MockCautelaApi({ latency: [0, 0] });
    const { token } = await login(api, "1020000001");
    const first = await api.turn(token, "No reconozco un cargo", null, "es");
    const asked = await api.turn(token, "2", first.conversation_id, "es");
    const picked = await api.recognize(token, asked.conversation_id, asked.recognition!.recognition_id, false);
    const done = await api.confirm(token, picked.conversation_id, picked.confirmation!.confirmation_id, true);
    const caseId = done.case!.case_id;

    const small = await api.turn(token, "Muchas gracias por todo", done.conversation_id, "es");
    expect(small).toMatchObject({ stage: "resolved", handoff_id: null });

    const after = await api.turn(token, "Quiero hablar con una persona.", done.conversation_id, "es");
    expect(after).toMatchObject({ stage: "handed_off", transfer_reason: "customer_requested_human", case: { case_id: caseId, verified: true } });
    expect(after.reply).toContain(caseId);
    const handoff = await api.getHandoff(after.handoff_id!);
    expect(handoff.transfer_reason).toEqual({ code: "customer_requested_human", rule_ids: ["SYN-HUMAN-001"] });
    expect(handoff.request.disputed_transaction_ids).toEqual(["TX00004182"]);
    expect(handoff.actions_taken).toContainEqual({ action: "open_dispute_case", status: "verified", record_id: caseId });
    expect(handoff.verified_facts.map((f) => f.source)).toEqual(expect.arrayContaining([`get_case_status:${caseId}`, "get_transaction:TX00004182"]));
    expect(handoff.open_questions.some((q) => q.includes(caseId))).toBe(true);
    expect((await api.listHandoffs()).filter((h) => h.handoff_id === after.handoff_id)).toHaveLength(1);
  });

  it("refuses a confirmation id it did not issue", async () => {
    const api = new MockCautelaApi({ latency: [0, 0] });
    const { token } = await login(api, "1020000001");
    const first = await api.turn(token, "No reconozco un cargo", null, "es");
    await expect(api.confirm(token, first.conversation_id, "forged", true)).rejects.toMatchObject({ code: "not_found" });
  });

  it("hands a failing write to a person with the reason tool_failure", async () => {
    const api = new MockCautelaApi({ latency: [0, 0] });
    const { token } = await login(api, "1020000003");
    const asked = await api.turn(token, "No reconozco un cobro de 85 mil pesos", null, "pt");
    const turn = await api.recognize(token, asked.conversation_id, asked.recognition!.recognition_id, false);
    const done = await api.confirm(token, turn.conversation_id, turn.confirmation!.confirmation_id, true);
    expect(done).toMatchObject({ stage: "handed_off", transfer_reason: "tool_failure", case: null });
    expect(done.reply).toMatch(/^Vou te passar para uma pessoa do banco/);
    const queue = await api.listHandoffs();
    expect(queue[0]).toMatchObject({ handoff_id: done.handoff_id, language: "pt", actions_taken: [{ action: "open_dispute_case", status: "failed" }] });
  });

  it("gives an unknown document no code, without revealing that it is unknown", async () => {
    const api = new MockCautelaApi({ latency: [0, 0] });
    const challenge = await api.startLogin("9999999999");
    expect(challenge.challenge_id).toMatch(/^ch_/);
    expect(await api.readTestOutbox(challenge.challenge_id)).toBeNull();
  });

  it("reports the end of a session as a session error", async () => {
    const api = new MockCautelaApi({ latency: [0, 0] });
    const { token } = await login(api, "1020000001");
    await api.logout(token);
    const err = await api.turn(token, "hola", null, "es").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).isSessionEnd).toBe(true);
  });

  it("detects the tampered record in the hash chain and keeps seeded traces intact", async () => {
    const api = new MockCautelaApi({ latency: [0, 0] });
    const intact = await api.getTrace("tr_5b2e9c41a7d03f6e");
    expect(intact.chain).toMatchObject({ status: "intact", first_bad_seq: null });
    const broken = await api.getTrace(TAMPER_TRACE_ID);
    expect(broken.chain).toMatchObject({ status: "broken", first_bad_seq: 2 });
  });
});

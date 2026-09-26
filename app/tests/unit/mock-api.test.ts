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

  it("opens a case only after the customer confirms, and reports it verified", async () => {
    const api = new MockCautelaApi({ latency: [0, 0] });
    const { token } = await login(api, "1020000001");
    const first = await api.turn(token, "No reconozco un cargo", null, "es");
    const picked = await api.turn(token, "2", first.conversation_id, "es");
    expect(picked.stage).toBe("awaiting_confirmation");
    expect(picked.case).toBeNull();
    const done = await api.confirm(token, picked.conversation_id, picked.confirmation!.confirmation_id, true);
    expect(done.stage).toBe("resolved");
    expect(done.case?.verified).toBe(true);
    const readBack = await api.getCaseStatus(token, done.case!.case_id);
    expect(readBack).toMatchObject({ status: "open", transaction_id: "TX00004182" });
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
    const turn = await api.turn(token, "No reconozco un cobro de 85 mil pesos", null, "pt");
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

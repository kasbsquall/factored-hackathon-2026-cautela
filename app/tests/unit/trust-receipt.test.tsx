import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
import { OutcomeReceipt } from "@/components/customer/outcome-receipt";
import type { Entry } from "@/components/customer/flow-types";
import { receiptChecks } from "@/components/customer/receipt-checks";
import type { CaseView, ChargeView, TurnResponse } from "@/lib/api/types";
import { CUSTOMER_COPY } from "@/lib/i18n/customer";

const es = CUSTOMER_COPY.es;
const en = CUSTOMER_COPY.en;
const CASE_ID = "CASE-294E237E628D";

function step(name: string, outcome: string): TurnResponse["trail"][number] {
  return { trace_id: "tr_1", step: name, outcome, detail: {}, rule_ids: [], latency_ms: 1 };
}

function turn(over: Partial<TurnResponse> = {}): TurnResponse {
  return {
    conversation_id: "cv_1", trace_id: "tr_1", language: "es", stage: "resolved", reply: "Listo.", reply_source: "template",
    options: [], recognition: null, confirmation: null,
    case: { case_id: CASE_ID, verified: true, claim_window: { rule_id: "CO-WINDOW-001", deadline: "2026-06-01" } },
    handoff_id: null, transfer_reason: null,
    trail: [step("confirm.answer", "accepted"), step("tool.open_dispute_case", "ok"), step("verify", "verified")],
    llm: { calls: 0, failed: 0, input_tokens: 0, output_tokens: 0, latency_ms: 0, cost_usd: 0, provider: null, model: null },
    latency_ms: 10,
    ...over,
  };
}

const charge: ChargeView = {
  amount: 49900, currency: "COP", merchant_name: "MERCANUBE", transaction_date: "2026-05-21T10:15:00", card_last4: "4821",
  card_type: "Debit Card", category: "Services", merchant_category: null, channel: "App", city: "Bogotá", country: "Colombia", transaction_status: "Approved", transaction_type: "Purchase",
};

const read: CaseView = { case_id: CASE_ID, transaction_id: "TX1", status: "open", created_at: "2026-05-30T12:00:00Z", policy_rule_ids: ["CO-WINDOW-001"] };
const earlier = turn({ stage: "awaiting_confirmation", case: null, trail: [step("recognize.answer", "not_recognized")] });

function entry(t: TurnResponse, caseView: CaseView | null, chargeView: ChargeView | null): Extract<Entry, { kind: "receipt" }> {
  return { id: "e9", kind: "receipt", turn: t, caseView, charge: null, chargeView };
}

function checkIds(): (string | null)[] {
  return screen.queryAllByRole("listitem").map((li) => li.getAttribute("data-check")).filter(Boolean);
}

describe("receipt checks", () => {
  it("lists every check when the response carries the data behind it", () => {
    const t = turn();
    const checks = receiptChecks({ turn: t, caseView: read, chargeView: charge, turns: [earlier, t], sessionUntil: "2026-05-30T12:15:00Z", copy: es, lang: "es" });
    expect(checks.map((c) => c.id)).toEqual(["identity", "charge", "confirmed", "case", "readBack"]);
    expect(checks.find((c) => c.id === "charge")?.value).toBe("MERCANUBE · 21 may 2026 · COP 49.900 · Débito •••• 4821");
    expect(checks.find((c) => c.id === "charge")?.spoken).toContain("tarjeta de débito terminada en 4821");
    expect(checks.find((c) => c.id === "confirmed")?.text).toBe(es.checkConfirmed);
  });

  it("drops a check whose data is missing instead of inventing it", () => {
    const t = turn({ trail: [], case: { case_id: CASE_ID, verified: true, claim_window: null } });
    const checks = receiptChecks({ turn: t, caseView: null, chargeView: null, turns: [t], sessionUntil: null, copy: es, lang: "es" });
    expect(checks.map((c) => c.id)).toEqual(["case"]);
  });

  it("does not claim a read-back that returned another case or a write the service did not verify", () => {
    const other = { ...read, case_id: "CASE-000000000000" };
    expect(receiptChecks({ turn: turn(), caseView: other, chargeView: null, turns: [], sessionUntil: null, copy: es, lang: "es" })
      .map((c) => c.id)).not.toContain("readBack");
    const unverified = turn({ case: { case_id: CASE_ID, verified: false, claim_window: null } });
    expect(receiptChecks({ turn: unverified, caseView: read, chargeView: null, turns: [], sessionUntil: null, copy: es, lang: "es" })
      .map((c) => c.id)).not.toContain("readBack");
  });

  it("says only that the dispute was confirmed when no recognition answer is on record", () => {
    const t = turn();
    const [confirmed] = receiptChecks({ turn: t, caseView: null, chargeView: null, turns: [t], sessionUntil: null, copy: en, lang: "en" })
      .filter((c) => c.id === "confirmed");
    expect(confirmed?.text).toBe(en.checkConfirmedOnly);
    expect(confirmed?.value).toBe(`“${en.confirm}”`);
  });
});

describe("OutcomeReceipt", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the checked facts, what happens next and a way to reach a person", () => {
    const t = turn();
    const onAskHuman = vi.fn();
    render(<OutcomeReceipt entry={entry(t, read, charge)} copy={es} lang="es" demoClock={null} turns={[earlier, t]}
      sessionUntil="2026-05-30T12:15:00Z" onAskHuman={onAskHuman} />);

    const receipt = screen.getByRole("region", { name: es.receiptOpen });
    expect(within(receipt).getByRole("region", { name: es.checksTitle })).toBeInTheDocument();
    expect(checkIds()).toEqual(["identity", "charge", "confirmed", "case", "readBack"]);
    expect(within(receipt).getByText("abierta", { exact: true })).toBeInTheDocument();
    const next = within(receipt).getByRole("region", { name: es.nextTitle });
    expect(within(next).getByText(es.nextOpen)).toBeInTheDocument();
    expect(within(next).getByText(/15 días o menos y 9 de cada 10 en 27 días o menos \(2\.862 quejas/)).toBeInTheDocument();
    expect(within(receipt).getByText("CO-WINDOW-001")).toBeInTheDocument();

    const ask = screen.getByRole("region", { name: es.askPersonTitle });
    fireEvent.click(within(ask).getByRole("button", { name: es.askHuman }));
    expect(onAskHuman).toHaveBeenCalledTimes(1);
  });

  it("shows only the case line when the read-back, charge and confirmation are missing", () => {
    const t = turn({ trail: [] });
    render(<OutcomeReceipt entry={entry(t, null, null)} copy={es} lang="es" demoClock={null} turns={[t]} sessionUntil={null} />);
    expect(screen.getByRole("region", { name: es.receiptNotVerified })).toBeInTheDocument();
    expect(checkIds()).toEqual(["case"]);
    expect(screen.queryByText(es.nextOpen)).toBeNull();
    expect(screen.getByText(es.notVerifiedBody)).toBeInTheDocument();
  });

  it("does not offer a person when one already has the case", () => {
    const t = turn({ stage: "handed_off", handoff_id: "ho_0123456789abcdef", transfer_reason: "amount_above_threshold" });
    render(<OutcomeReceipt entry={entry(t, { ...read, status: "pending_human_review" }, charge)} copy={es} lang="es" demoClock={null}
      turns={[t]} sessionUntil={null} onAskHuman={() => undefined} />);
    expect(screen.getByRole("region", { name: es.receiptReview })).toBeInTheDocument();
    expect(screen.getByText(es.confirmReviewLive)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: es.askHuman })).toBeNull();
  });

  it("copies the case number with the clipboard API", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const t = turn();
    render(<OutcomeReceipt entry={entry(t, read, null)} copy={es} lang="es" demoClock={null} turns={[t]} sessionUntil={null} />);
    fireEvent.click(screen.getByRole("button", { name: es.copyCase }));
    await waitFor(() => expect(screen.getByRole("button", { name: es.copied })).toBeInTheDocument());
    expect(writeText).toHaveBeenCalledWith(CASE_ID);
    expect(screen.getByRole("status")).toHaveTextContent(es.copiedStatus);
  });

  it("falls back to the copy command, and says so when nothing could be copied", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    const exec = vi.fn().mockReturnValue(false);
    Object.defineProperty(document, "execCommand", { value: exec, configurable: true });
    const t = turn();
    render(<OutcomeReceipt entry={entry(t, read, null)} copy={es} lang="es" demoClock={null} turns={[t]} sessionUntil={null} />);
    fireEvent.click(screen.getByRole("button", { name: es.copyCase }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(es.copyFailed));
    expect(exec).toHaveBeenCalledWith("copy");
  });
});

import { fireEvent, render, screen, within } from "@testing-library/react";
import { OptionList } from "@/components/customer/option-list";
import type { ChargeView, OptionView } from "@/lib/api/types";
import { CUSTOMER_COPY } from "@/lib/i18n/customer";

const charge = (c: Partial<ChargeView>): ChargeView => ({
  transaction_date: null, amount: null, currency: null, merchant_name: null, merchant_category: null, category: null,
  channel: null, city: null, country: null, card_type: null, card_last4: null, transaction_type: "Purchase",
  transaction_status: "Approved", ...c,
});

const options: OptionView[] = [
  {
    index: 1, kind: "transaction", label: "24/05/2026, Farmacia San Rafael, 2675.59 MXN",
    charge: charge({ transaction_date: "2026-05-24T18:05:00", amount: 2675.59, currency: "MXN", merchant_name: "Farmacia San Rafael",
      channel: "POS", city: "Mérida", card_type: "Debit Card", card_last4: "4821" }),
    reasons: [{ code: "amount_close", label: "Monto a 3% del que indicaste", value: 3 }, { code: "date_same_day", label: "El mismo día que indicaste", value: null }],
  },
  {
    index: 2, kind: "transaction", label: "18/05/2026, Viajes Andinos, 7259.85 MXN",
    charge: charge({ transaction_date: "2026-05-18T09:31:00", amount: 7259.85, currency: "MXN", merchant_name: "Viajes Andinos", channel: "Web" }),
    reasons: [],
  },
];
const entry = { id: "e1", kind: "options" as const, options };

describe("OptionList", () => {
  it("shows every option the service ranked plus a way out, and picks none by itself", () => {
    render(<OptionList entry={entry} copy={CUSTOMER_COPY.es} lang="es" disabled={false} onPick={() => undefined} />);
    const group = screen.getByRole("group", { name: CUSTOMER_COPY.es.optionsTitle });
    const buttons = within(group).getAllByRole("button");
    expect(buttons).toHaveLength(3);
    for (const b of buttons) expect(b).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Farmacia San Rafael")).toBeInTheDocument();
    expect(screen.getByText("MXN 2,675.59")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ninguno de estos/ })).toBeInTheDocument();
  });

  it("shows the charge data and the match reasons the service sent, and says so when none fired", () => {
    render(<OptionList entry={entry} copy={CUSTOMER_COPY.es} lang="es" disabled={false} onPick={() => undefined} />);
    const first = screen.getByRole("button", { name: /Farmacia San Rafael/ });
    expect(within(first).getByText("Terminal en el comercio")).toBeInTheDocument();
    expect(within(first).getByText("Mérida")).toBeInTheDocument();
    expect(within(first).getByText("Débito •••• 4821")).toBeInTheDocument();
    const reasons = within(first).getByRole("list", { name: "En qué coincide" });
    expect(within(reasons).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Monto a 3% del que indicaste", "El mismo día que indicaste"]);
    const second = screen.getByRole("button", { name: /Viajes Andinos/ });
    expect(within(second).getByText(CUSTOMER_COPY.es.noReasons)).toBeInTheDocument();
  });

  it("sends the chosen option and locks the list while a turn is running", () => {
    const picks: (number | null)[] = [];
    const { rerender } = render(<OptionList entry={entry} copy={CUSTOMER_COPY.pt} lang="pt" disabled={false} onPick={(o) => picks.push(o?.index ?? null)} />);
    fireEvent.click(screen.getByRole("button", { name: /Viajes Andinos/ }));
    expect(picks).toEqual([2]);
    rerender(<OptionList entry={entry} copy={CUSTOMER_COPY.pt} lang="pt" disabled onPick={(o) => picks.push(o?.index ?? null)} />);
    fireEvent.click(screen.getByRole("button", { name: /Nenhuma destas/ }));
    expect(picks).toEqual([2]);
  });

  it("falls back to the label when an option has no structured charge", () => {
    const bare = { ...entry, options: [{ index: 1, kind: "card" as const, label: "**** 4821", charge: null, reasons: [] }] };
    render(<OptionList entry={bare} copy={CUSTOMER_COPY.es} lang="es" disabled={false} onPick={() => undefined} />);
    expect(screen.getByText("**** 4821")).toBeInTheDocument();
  });
});

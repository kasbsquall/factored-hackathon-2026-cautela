import { fireEvent, render, screen } from "@testing-library/react";
import { OptionList } from "@/components/customer/option-list";
import { CUSTOMER_COPY } from "@/lib/i18n/customer";

const entry = {
  id: "e1",
  kind: "options" as const,
  options: [
    { index: 1, kind: "transaction" as const, label: "24/05/2026, Farmacia San Rafael, 2675.59 MXN" },
    { index: 2, kind: "transaction" as const, label: "18/05/2026, Viajes Andinos, 7259.85 MXN" },
  ],
};

describe("OptionList", () => {
  it("shows every option the service ranked plus a way out, and picks none by itself", () => {
    render(<OptionList entry={entry} copy={CUSTOMER_COPY.es} lang="es" disabled={false} onPick={() => undefined} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(3);
    for (const b of buttons) expect(b).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Farmacia San Rafael")).toBeInTheDocument();
    expect(screen.getByText("MXN 2,675.59")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ninguno de estos/ })).toBeInTheDocument();
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
});

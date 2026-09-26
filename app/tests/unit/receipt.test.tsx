import { render, screen } from "@testing-library/react";
import { Receipt } from "@/components/ui/receipt";
import { StatusChip } from "@/components/ui/status-chip";

describe("Receipt and StatusChip", () => {
  it("renders every row of a receipt as a term and its value", () => {
    render(<Receipt kind="ok" title="Disputa abierta" rows={[{ label: "Caso", value: "CASE-1" }, { label: "Regla", value: "CO-WINDOW-001" }]} />);
    expect(screen.getByRole("region", { name: "Disputa abierta" })).toBeInTheDocument();
    expect(screen.getByText("Caso").nextSibling).toHaveTextContent("CASE-1");
    expect(screen.getByText("CO-WINDOW-001")).toBeInTheDocument();
  });

  it("never conveys status by color alone: the chip carries text", () => {
    render(<StatusChip kind="bad">Failed</StatusChip>);
    expect(screen.getByText("Failed")).toBeVisible();
  });
});

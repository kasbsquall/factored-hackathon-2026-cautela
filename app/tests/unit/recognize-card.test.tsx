import { fireEvent, render, screen } from "@testing-library/react";
import { RecognizeCard } from "@/components/customer/recognize-card";
import type { RecognizeState } from "@/components/customer/flow-types";
import type { RecognitionView } from "@/lib/api/types";
import { CUSTOMER_COPY } from "@/lib/i18n/customer";

const recognition: RecognitionView = {
  recognition_id: "rc_1",
  label: "22/09/2026, MERCANUBE*APP SYN, 48900.00 COP",
  charge: {
    transaction_date: "2026-09-22T19:42:00", amount: 48900, currency: "COP", merchant_name: "MERCANUBE*APP SYN",
    merchant_category: "5399", category: "Other", channel: "App", city: "Bogotá", country: "Colombia",
    card_type: "Debit Card", card_last4: "4821", transaction_type: "Purchase", transaction_status: "Approved",
  },
  reasons: [{ code: "amount_close", label: "Monto a 2% del que indicaste", value: 2 }],
  claim_window: { rule_id: "CO-WINDOW-001", deadline: "2026-09-29" },
};

function renderCard(state: RecognizeState, onAnswer: (r: boolean) => void = () => undefined, lang: "es" | "pt" = "es",
  demoClock: string | null = null, charge: Partial<RecognitionView["charge"]> = {}) {
  const entry = { id: "e9", kind: "recognize" as const, recognition: { ...recognition, charge: { ...recognition.charge, ...charge } },
    conversationId: "cv_1", state };
  return render(<RecognizeCard entry={entry} copy={CUSTOMER_COPY[lang]} lang={lang} demoClock={demoClock} onAnswer={onAnswer} />);
}

describe("RecognizeCard", () => {
  it("does not repeat a category that the source also wrote as the merchant category", () => {
    renderCard("pending", undefined, "es", null, { category: "Food", merchant_category: "Food" });
    expect(screen.getByText("Alimentación")).toBeInTheDocument();
    expect(screen.queryByText(/MCC/)).toBeNull();
  });

  it("says the claim deadline is counted from the demo date when the service runs on one", () => {
    renderCard("pending", undefined, "es", "2026-06-19T12:16:58Z");
    expect(screen.getByText(/Contado desde la fecha de la demo, 19 jun 2026/)).toBeInTheDocument();
  });

  it("shows the verified charge, its evidence and the claim deadline before any dispute", () => {
    renderCard("pending");
    expect(screen.getByRole("heading", { name: "¿Reconoces este cargo?" })).toBeInTheDocument();
    expect(screen.getByText("MERCANUBE*APP SYN")).toBeInTheDocument();
    expect(screen.getByText(/^COP\s48\.900$/)).toBeInTheDocument();
    expect(screen.getByText("Otros · MCC 5399")).toBeInTheDocument();
    expect(screen.getByText("App del comercio")).toBeInTheDocument();
    expect(screen.getByText("Bogotá, Colombia")).toBeInTheDocument();
    expect(screen.getByText("Débito •••• 4821")).toBeInTheDocument();
    expect(screen.getByText(/19:42/)).toBeInTheDocument();
    expect(screen.getByText("Monto a 2% del que indicaste")).toBeInTheDocument();
    expect(screen.getByText(/CO-WINDOW-001/)).toBeInTheDocument();
  });

  it("sends each answer once and locks both buttons after", () => {
    const answers: boolean[] = [];
    const { unmount } = renderCard("pending", (r) => answers.push(r));
    fireEvent.click(screen.getByRole("button", { name: "Sí, lo reconozco" }));
    fireEvent.click(screen.getByRole("button", { name: /No lo reconozco/ }));
    expect(answers).toEqual([true, false]);
    unmount();
    renderCard("no", (r) => answers.push(r));
    fireEvent.click(screen.getByRole("button", { name: "Sí, lo reconozco" }));
    expect(answers).toEqual([true, false]);
    expect(screen.getByRole("button", { name: /No lo reconozco/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("speaks Portuguese in a Portuguese conversation", () => {
    renderCard("pending", undefined, "pt");
    expect(screen.getByRole("heading", { name: "Você reconhece esta cobrança?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sim, reconheço" })).toBeInTheDocument();
    expect(screen.getByText("Aplicativo da loja")).toBeInTheDocument();
  });
});

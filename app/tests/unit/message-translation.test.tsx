import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MessageTranslation, englishSource } from "@/components/customer/message-translation";
import type { Entry } from "@/components/customer/flow-types";
import { ApiError, type Translation } from "@/lib/api/client";
import { CUSTOMER_COPY } from "@/lib/i18n/customer";

type Message = Extract<Entry, { kind: "user" | "system" }>;

const templateReply: Message = {
  id: "s1", kind: "system", say: () => "Entendido, no registré nada.",
  reply: { source: "template", template: "declined", language: "es" },
};
const modelReply: Message = {
  id: "s2", kind: "system", say: () => "Listo, ya quedó tu caso.",
  reply: { source: "llm", template: "resolved", language: "es" },
};
const typed: Message = { id: "u1", kind: "user", text: "me cobraron dos veces en el super" };

function setup(entry: Message, translate = vi.fn<(role: "customer" | "assistant", text: string) => Promise<Translation>>()) {
  render(<MessageTranslation entry={entry} talk="es" translate={translate} conversationId={`cv_${entry.id}_${Math.random()}`} />);
  return translate;
}

describe("englishSource", () => {
  it("uses the app's own copy for the greeting and for texts the buttons send", () => {
    const greeting: Message = { id: "g", kind: "system", say: (c) => c.greeting };
    expect(englishSource(greeting, "pt")).toEqual({ kind: "copy", text: CUSTOMER_COPY.en.greeting });
    expect(englishSource({ id: "n", kind: "user", text: CUSTOMER_COPY.pt.none }, "pt")).toEqual({ kind: "copy", text: "None of these" });
    expect(englishSource({ id: "h", kind: "user", text: CUSTOMER_COPY.es.askHumanMessage }, "es")).toEqual({ kind: "copy", text: "I want to talk to a person." });
  });

  it("translates a template reply deterministically and sends free text to the service", () => {
    expect(englishSource(templateReply, "es")).toEqual({ kind: "template", text: "Understood, I did not file anything.", template: "declined" });
    expect(englishSource(modelReply, "es")).toEqual({ kind: "machine" });
    expect(englishSource(typed, "es")).toEqual({ kind: "machine" });
  });

  it("offers nothing for an option pick, whose text is the charge label", () => {
    expect(englishSource({ id: "o", kind: "user", text: "30/05/2026, Marketplace Uno, 5335.32 MXN", option: 1 }, "es")).toBeNull();
  });
});

describe("MessageTranslation", () => {
  it("shows a template's English without calling the service, labeled as deterministic", () => {
    const translate = setup(templateReply);
    const toggle = screen.getByRole("button", { name: "Show English translation" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Hide English translation" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Understood, I did not file anything.")).toBeInTheDocument();
    expect(screen.getByText(/fixed reply template "declined", no model involved/)).toBeInTheDocument();
    expect(translate).not.toHaveBeenCalled();
  });

  it("asks the service for free text once, shows a skeleton, then a labeled machine translation", async () => {
    let resolve!: (t: Translation) => void;
    const translate = setup(typed, vi.fn(() => new Promise<Translation>((r) => { resolve = r; })));
    fireEvent.click(screen.getByRole("button", { name: "Show English translation" }));
    expect(translate).toHaveBeenCalledWith("customer", typed.text);
    expect(screen.getByText("Translating")).toBeInTheDocument();
    resolve({ text: "they charged me twice at the supermarket", method: "machine", masked: true, provider: "openai", model: "gpt-x", cached: false });
    expect(await screen.findByText("they charged me twice at the supermarket")).toBeInTheDocument();
    expect(screen.getByText("Machine translation · openai · gpt-x · personal data masked before it reached the model")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide English translation" }));
    fireEvent.click(screen.getByRole("button", { name: "Show English translation" }));
    expect(translate).toHaveBeenCalledTimes(1);
  });

  it("says plainly when the service has no model, and offers a retry for other failures", async () => {
    setup(typed, vi.fn(() => Promise.reject(new ApiError("translation_unavailable", "no model"))));
    fireEvent.click(screen.getByRole("button", { name: "Show English translation" }));
    expect(await screen.findByText(/mock mode has no language model|no language model available right now/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("retries after a network failure", async () => {
    const translate = vi.fn()
      .mockRejectedValueOnce(new ApiError("network", "down"))
      .mockResolvedValueOnce({ text: "ok in English", method: "machine", masked: false, provider: null, model: null, cached: true });
    setup(modelReply, translate);
    fireEvent.click(screen.getByRole("button", { name: "Show English translation" }));
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("ok in English")).toBeInTheDocument());
    expect(translate).toHaveBeenCalledWith("assistant", "Listo, ya quedó tu caso.");
  });
});

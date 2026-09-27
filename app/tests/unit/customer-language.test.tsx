import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CustomerApp } from "@/components/customer/customer-app";
import { setApiForTests } from "@/lib/api";
import type { TestIdentity } from "@/lib/api/client";
import { MockCautelaApi } from "@/lib/api/mock/mock-api";
import { scenarioLanguage } from "@/lib/i18n/customer";

const PORTUGUESE: TestIdentity = { document: "3000000001", label: "Portuguese conversation", scenario: "portuguese" };
const NORMAL: TestIdentity = { document: "3000000002", label: "Normal", scenario: "normal" };

/** The mock service with the live demo's Portuguese customer on its login list. */
class WithPortuguese extends MockCautelaApi {
  override async listTestIdentities(): Promise<TestIdentity[]> {
    return [PORTUGUESE, NORMAL];
  }
}

function pressed(name: RegExp) {
  return screen.getByRole("button", { name }).getAttribute("aria-pressed");
}

async function renderApp() {
  render(<CustomerApp />);
  await screen.findByRole("button", { name: /Conversación en portugués|Conversa em português/ });
}

describe("conversation language of the test customers", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setApiForTests(new WithPortuguese({ latency: [0, 0] }));
  });
  afterEach(() => setApiForTests(null));

  it("maps only the Portuguese scenario to a language", () => {
    expect(scenarioLanguage("portuguese")).toBe("pt");
    expect(scenarioLanguage("normal")).toBeNull();
  });

  it("switches to PT when the Portuguese customer is picked, and back to ES for the next customer", async () => {
    await renderApp();
    expect(pressed(/^ES/)).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: /Conversación en portugués/ }));
    await waitFor(() => expect(pressed(/^PT/)).toBe("true"));
    expect(window.localStorage.getItem("cautela.lang")).toBe("pt:pick");

    fireEvent.click(screen.getByRole("button", { name: /Uma cobrança clara/ }));
    await waitFor(() => expect(pressed(/^ES/)).toBe("true"));
  });

  it("keeps a language chosen with the switch when a customer without one is picked", async () => {
    await renderApp();
    fireEvent.click(screen.getByRole("button", { name: /^PT/ }));
    fireEvent.click(screen.getByRole("button", { name: /Uma cobrança clara/ }));
    expect(pressed(/^PT/)).toBe("true");
  });

  it("remembers the language for the next visit", async () => {
    window.localStorage.setItem("cautela.lang", "pt");
    await renderApp();
    expect(pressed(/^PT/)).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /Uma cobrança clara/ }));
    expect(pressed(/^PT/)).toBe("true");
  });

  it("remembers that the Portuguese customer set the language, so the next customer goes back to ES", async () => {
    window.localStorage.setItem("cautela.lang", "pt:pick");
    await renderApp();
    expect(pressed(/^PT/)).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /Uma cobrança clara/ }));
    await waitFor(() => expect(pressed(/^ES/)).toBe("true"));
  });
});

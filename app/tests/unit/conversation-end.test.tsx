import { fireEvent, render, screen, within } from "@testing-library/react";
import { Conversation } from "@/components/customer/conversation";
import { withoutOptionLines } from "@/components/customer/use-turn-flow";
import { setApiForTests } from "@/lib/api";
import { MockCautelaApi } from "@/lib/api/mock/mock-api";
import { CUSTOMER_COPY } from "@/lib/i18n/customer";

const en = CUSTOMER_COPY.en;
const es = CUSTOMER_COPY.es;

async function start(document: string) {
  const api = new MockCautelaApi({ latency: [0, 0] });
  setApiForTests(api);
  const challenge = await api.startLogin(document);
  const grant = await api.verifyOtp(challenge.challenge_id, (await api.readTestOutbox(challenge.challenge_id)) ?? "");
  const identity = (await api.listTestIdentities()).find((i) => i.document === document) ?? null;
  render(
    <Conversation auth={{ token: grant.token, expiresAt: grant.expires_at, identity }} copy={en} talk={es} lang="es" ui="en"
      demoClock={null} onSessionEnd={() => undefined} onStage={() => undefined} onTrail={() => undefined} />,
  );
}

describe("the composer after the conversation ends", () => {
  beforeAll(() => {
    // jsdom has no layout: scrolling is a no-op here.
    Element.prototype.scrollIntoView = () => undefined;
    window.matchMedia ??= ((query: string) => ({ matches: false, media: query }) as MediaQueryList);
  });
  afterEach(() => setApiForTests(null));

  it("offers a new review instead of a person or a message box once the case is settled", async () => {
    await start("1020000002");
    fireEvent.click(screen.getByRole("button", { name: /casi 10 mil pesos/ }));
    fireEvent.click(await screen.findByRole("button", { name: en.recognizeYes }));
    await screen.findByRole("region", { name: en.receiptRecognized });

    expect(screen.queryByRole("button", { name: en.askHuman })).toBeNull();
    expect(screen.queryByRole("textbox", { name: en.composerLabel })).toBeNull();
    expect(screen.getByText(en.closedNote)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: en.restart }));
    expect(await screen.findByRole("button", { name: en.askHuman })).toBeInTheDocument();
  });

  it("keeps the message box after a handoff and says messages go to the case", async () => {
    await start("1020000002");
    fireEvent.click(screen.getByRole("button", { name: en.askHuman }));
    await screen.findByText(en.handedOffNote);

    const form = screen.getByRole("textbox", { name: en.composerLabel }).closest("form")!;
    expect(within(form).getByRole("textbox")).toHaveAttribute("placeholder", en.handedOffPlaceholder);
    expect(screen.queryByRole("button", { name: en.askHuman })).toBeNull();
    expect(within(form).getByRole("button", { name: en.restart })).toBeInTheDocument();
  });
});

describe("withoutOptionLines", () => {
  it("drops the numbered options and the blank lines around them", () => {
    const reply = "Encontré más de un cargo que coincide. ¿Cuál no reconoces?\n\n1) Cable TV\n2) Servicios\n3) Super Ahorro\n\nSi no es ninguno, dímelo.";
    expect(withoutOptionLines(reply)).toBe("Encontré más de un cargo que coincide. ¿Cuál no reconoces?\nSi no es ninguno, dímelo.");
  });
});

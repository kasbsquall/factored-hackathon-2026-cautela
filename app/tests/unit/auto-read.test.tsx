import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { AutoReadBar, useAutoRead } from "@/components/customer/auto-read";
import { Conversation } from "@/components/customer/conversation";
import { setApiForTests } from "@/lib/api";
import { MockCautelaApi } from "@/lib/api/mock/mock-api";
import { CUSTOMER_COPY } from "@/lib/i18n/customer";
import { stopSpeech } from "@/lib/speech";
import { installSpeech, removeSpeech, voice } from "./fake-speech";

const es = CUSTOMER_COPY.es;
const en = CUSTOMER_COPY.en;
const KEY = "cautela.read-aloud";

function Harness() {
  const [on, setOn] = useAutoRead();
  return <AutoReadBar on={on} onChange={setOn} lang="es" copy={es} />;
}

describe("read replies aloud switch", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => {
    stopSpeech();
    removeSpeech();
    vi.restoreAllMocks();
  });

  it("turns on and off and is remembered by this browser", () => {
    installSpeech([voice("es-MX")]);
    const { unmount } = render(<Harness />);
    const toggle = screen.getByRole("switch", { name: es.autoRead });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(window.localStorage.getItem(KEY)).toBe("on");
    unmount();

    render(<Harness />);
    expect(screen.getByRole("switch", { name: es.autoRead })).toHaveAttribute("aria-checked", "true");
  });

  it("still works for the page when storage is blocked", () => {
    installSpeech([voice("es-MX")]);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<Harness />);
    const toggle = screen.getByRole("switch", { name: es.autoRead });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
  });

  it("is disabled with the reason when the device has no voice for the conversation language", () => {
    removeSpeech();
    render(<Harness />);
    const toggle = screen.getByRole("switch", { name: es.autoRead });
    expect(toggle).toHaveAttribute("aria-disabled", "true");
    expect(toggle).toHaveAccessibleDescription(es.noVoice("español"));
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });
});

describe("auto-read in the conversation", () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = () => undefined;
    window.matchMedia ??= ((query: string) => ({ matches: false, media: query }) as MediaQueryList);
  });
  afterEach(() => {
    stopSpeech();
    removeSpeech();
    setApiForTests(null);
    window.localStorage.clear();
  });

  async function start() {
    const api = new MockCautelaApi({ latency: [0, 0] });
    setApiForTests(api);
    const challenge = await api.startLogin("1020000001");
    const grant = await api.verifyOtp(challenge.challenge_id, (await api.readTestOutbox(challenge.challenge_id)) ?? "");
    const identity = (await api.listTestIdentities()).find((i) => i.document === "1020000001") ?? null;
    render(
      <Conversation auth={{ token: grant.token, expiresAt: grant.expires_at, identity }} copy={en} talk={es} lang="es" ui="en"
        demoClock={null} onSessionEnd={() => undefined} onStage={() => undefined} onTrail={() => undefined} />,
    );
  }

  it("reads the greeting and each new reply in the conversation language", async () => {
    window.localStorage.setItem(KEY, "on");
    const synth = installSpeech([voice("en-US"), voice("es-CO")]);
    await start();
    await waitFor(() => expect(synth.spoken().map((u) => u.text)).toEqual([es.greeting]));
    expect(synth.spoken()[0]?.voice?.lang).toBe("es-CO");

    fireEvent.click(screen.getByRole("button", { name: /Me cobraron algo/ }));
    await screen.findByRole("group", { name: en.optionsTitle });
    await waitFor(() => expect(synth.spoken()).toHaveLength(2));
    expect(synth.spoken()[1]?.text).toMatch(/Encontré más de un cargo/);
  });

  it("reads nothing while the switch is off, and not the backlog when it is turned on", async () => {
    const synth = installSpeech([voice("es-MX")]);
    await start();
    fireEvent.click(screen.getByRole("button", { name: /Me cobraron algo/ }));
    await screen.findByRole("group", { name: en.optionsTitle });
    fireEvent.click(screen.getByRole("switch", { name: en.autoRead }));
    expect(synth.spoken()).toHaveLength(0);
  });

  it("offers read-aloud on every assistant message", async () => {
    installSpeech([voice("es-MX")]);
    await start();
    expect(screen.getAllByRole("button", { name: en.listenMessage })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /Me cobraron algo/ }));
    await screen.findByRole("group", { name: en.optionsTitle });
    expect(screen.getAllByRole("button", { name: en.listenMessage })).toHaveLength(2);
  });
});

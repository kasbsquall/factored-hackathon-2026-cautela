import { act, fireEvent, render, screen } from "@testing-library/react";
import { ReadAloud } from "@/components/customer/read-aloud";
import { CUSTOMER_COPY } from "@/lib/i18n/customer";
import { pickVoice, spellId, spokenTextOf, stopSpeech } from "@/lib/speech";
import { installSpeech, removeSpeech, voice } from "./fake-speech";

const es = CUSTOMER_COPY.es;

function listen(id = "m1", text = "Hola. Cuéntame qué cargo no reconoces.") {
  return <ReadAloud id={id} text={() => text} lang="es" copy={es} label={es.listenMessage} />;
}

describe("pickVoice", () => {
  it("prefers a Latin American regional voice, then any voice of the language", () => {
    const spain = voice("es-ES");
    const mexico = voice("es-MX");
    expect(pickVoice([spain, mexico], "es")).toBe(mexico);
    expect(pickVoice([spain], "es")).toBe(spain);
    expect(pickVoice([voice("pt-PT"), voice("pt-BR")], "pt")?.lang).toBe("pt-BR");
  });

  it("never picks a network voice, which would send the text to a server", () => {
    expect(pickVoice([voice("es-MX", false), voice("en-US")], "es")).toBeNull();
  });
});

describe("ReadAloud", () => {
  afterEach(() => {
    stopSpeech();
    removeSpeech();
  });

  it("is absent when the browser has no speech synthesis", () => {
    removeSpeech();
    const { container } = render(listen());
    expect(container).toBeEmptyDOMElement();
  });

  it("is absent when the device has no local voice for the language", () => {
    installSpeech([voice("en-US"), voice("es-MX", false)]);
    const { container } = render(listen());
    expect(container).toBeEmptyDOMElement();
  });

  it("reads the text with the matching voice, then pauses, resumes and stops", () => {
    const synth = installSpeech([voice("en-US"), voice("es-ES"), voice("es-MX")]);
    render(listen());

    fireEvent.click(screen.getByRole("button", { name: es.listenMessage }));
    const [utterance] = synth.spoken();
    expect(utterance?.text).toBe("Hola. Cuéntame qué cargo no reconoces.");
    expect(utterance?.voice?.lang).toBe("es-MX");

    fireEvent.click(screen.getByRole("button", { name: es.pause }));
    expect(synth.pause).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: es.resume }));
    expect(synth.resume).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: es.stop }));
    expect(synth.cancel).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: es.listenMessage })).toHaveFocus();
    expect(screen.queryByRole("button", { name: es.stop })).toBeNull();
  });

  it("goes back to idle when the utterance ends", () => {
    const synth = installSpeech([voice("es-MX")]);
    render(listen());
    fireEvent.click(screen.getByRole("button", { name: es.listenMessage }));
    act(() => synth.spoken()[0]?.onend?.());
    expect(screen.getByRole("button", { name: es.listenMessage })).toBeInTheDocument();
  });

  it("plays one utterance at a time: starting another stops the first", () => {
    const synth = installSpeech([voice("es-MX")]);
    render(<>{listen("a", "Primero")}{listen("b", "Segundo")}</>);
    const [first, second] = screen.getAllByRole("button", { name: es.listenMessage });
    fireEvent.click(first!);
    fireEvent.click(second!);
    expect(synth.cancel).toHaveBeenCalledTimes(2);
    expect(synth.spoken().map((u) => u.text)).toEqual(["Primero", "Segundo"]);
    // Only the second control shows pause; the first is back to listen.
    expect(screen.getAllByRole("button", { name: es.pause })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: es.listenMessage })).toHaveLength(1);
    // The first utterance ending late does not reset the second.
    act(() => synth.spoken()[0]?.onend?.());
    expect(screen.getByRole("button", { name: es.pause })).toBeInTheDocument();
  });
});

describe("spoken text", () => {
  it("reads blocks as sentences, skips buttons and icons, and uses spoken forms", () => {
    const root = document.createElement("section");
    root.innerHTML = `<h3><svg aria-hidden="true"></svg>Disputa abierta</h3>
      <span data-speech="CASE, 1 2">CASE-12</span><button>Copiar número</button>
      <dl><div><dt>Plazo</dt><dd>hasta el 22/08/2026<small>90 días</small></dd></div></dl>
      <p data-speech-skip>tr_abc</p>`;
    expect(spokenTextOf(root)).toBe("Disputa abierta. CASE, 1 2. Plazo: hasta el 22/08/2026. 90 días.");
  });

  it("spells a case id so it can be written down", () => {
    expect(spellId("CASE-294E237E628D")).toBe("CASE, 2 9 4 E 2 3 7 E 6 2 8 D");
  });
});

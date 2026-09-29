import { vi } from "vitest";

/** A stand-in for the Web Speech API: jsdom has no speechSynthesis. */
export class FakeUtterance {
  voice: SpeechSynthesisVoice | null = null;
  lang = "";
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public text: string) {}
}

export function voice(lang: string, localService = true, name = `${lang} voice`): SpeechSynthesisVoice {
  return { lang, localService, name, default: false, voiceURI: name } as SpeechSynthesisVoice;
}

export interface FakeSynth {
  speak: ReturnType<typeof vi.fn>;
  cancel: ReturnType<typeof vi.fn>;
  pause: ReturnType<typeof vi.fn>;
  resume: ReturnType<typeof vi.fn>;
  spoken: () => FakeUtterance[];
}

/** Installs a fake speechSynthesis with these voices; returns it so a test can inspect what was spoken. */
export function installSpeech(voices: SpeechSynthesisVoice[]): FakeSynth {
  const speak = vi.fn();
  const synth = {
    speak,
    cancel: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    getVoices: () => voices,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };
  Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true, writable: true });
  Object.defineProperty(window, "SpeechSynthesisUtterance", { value: FakeUtterance, configurable: true, writable: true });
  return { ...synth, spoken: () => speak.mock.calls.map((c) => c[0] as FakeUtterance) };
}

export function removeSpeech(): void {
  Reflect.deleteProperty(window, "speechSynthesis");
  Reflect.deleteProperty(window, "SpeechSynthesisUtterance");
}

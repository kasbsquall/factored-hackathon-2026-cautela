"use client";

import { useSyncExternalStore } from "react";
import type { UiLang } from "@/lib/i18n/customer";

/**
 * Read-aloud with the browser's own speech synthesis (Web Speech API). Only voices the browser marks as local are
 * used: a network voice (Chrome's "Google" voices) would send the text to a server, so a language with no local
 * voice counts as unavailable. One utterance plays at a time across the whole page.
 */

export type SpeechStatus = "idle" | "speaking" | "paused";

export interface SpeechState {
  /** Which control started the current utterance, or null. */
  id: string | null;
  status: SpeechStatus;
}

/** Regional voices first, in the order the demo's countries appear; then any voice of the language. */
const PREFERRED: Record<UiLang, string[]> = {
  es: ["es-mx", "es-co", "es-ar", "es-us", "es-419"],
  pt: ["pt-br"],
  en: ["en-us", "en-gb"],
};

const IDLE: SpeechState = { id: null, status: "idle" };
const NO_VOICES: SpeechSynthesisVoice[] = [];

export function speechAvailable(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && typeof window.SpeechSynthesisUtterance === "function";
}

function tagOf(voice: SpeechSynthesisVoice): string {
  return voice.lang.replace("_", "-").toLowerCase();
}

/** The on-device voice for a language, or null when the device has none. */
export function pickVoice(voices: SpeechSynthesisVoice[], lang: UiLang): SpeechSynthesisVoice | null {
  const local = voices.filter((v) => v.localService);
  for (const want of PREFERRED[lang]) {
    const hit = local.find((v) => tagOf(v) === want);
    if (hit) return hit;
  }
  return local.find((v) => tagOf(v) === lang || tagOf(v).startsWith(`${lang}-`)) ?? null;
}

/* ---------- voices (they load asynchronously in most browsers) ---------- */

let voices: SpeechSynthesisVoice[] = NO_VOICES;

function subscribeVoices(onChange: () => void): () => void {
  if (!speechAvailable()) return () => undefined;
  const synth = window.speechSynthesis;
  const load = () => {
    voices = synth.getVoices();
    onChange();
  };
  load();
  synth.addEventListener?.("voiceschanged", load);
  return () => synth.removeEventListener?.("voiceschanged", load);
}

/** True when this device can read the language aloud without sending text anywhere. False on the server. */
export function useCanSpeak(lang: UiLang): boolean {
  const list = useSyncExternalStore(subscribeVoices, () => (speechAvailable() ? voices : NO_VOICES), () => NO_VOICES);
  return pickVoice(list, lang) !== null;
}

/* ---------- the single utterance ---------- */

let state: SpeechState = IDLE;
let current: SpeechSynthesisUtterance | null = null;
const listeners = new Set<() => void>();

function setState(next: SpeechState): void {
  state = next;
  listeners.forEach((listener) => listener());
}

function subscribeSpeech(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useSpeechState(): SpeechState {
  return useSyncExternalStore(subscribeSpeech, () => state, () => IDLE);
}

/** Reads `text` aloud, stopping whatever was playing. Returns false when there is no local voice for `lang`. */
export function speak(id: string, text: string, lang: UiLang): boolean {
  if (!speechAvailable() || !text.trim()) return false;
  const synth = window.speechSynthesis;
  const voice = pickVoice(synth.getVoices(), lang);
  if (!voice) return false;
  synth.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.voice = voice;
  utterance.lang = voice.lang;
  // cancel() ends the previous utterance asynchronously: only the current one may reset the state.
  const finish = () => {
    if (current !== utterance) return;
    current = null;
    setState(IDLE);
  };
  utterance.onend = finish;
  utterance.onerror = finish;
  current = utterance;
  setState({ id, status: "speaking" });
  synth.speak(utterance);
  return true;
}

export function pauseSpeech(): void {
  if (state.status !== "speaking" || !speechAvailable()) return;
  window.speechSynthesis.pause();
  setState({ ...state, status: "paused" });
}

export function resumeSpeech(): void {
  if (state.status !== "paused" || !speechAvailable()) return;
  window.speechSynthesis.resume();
  setState({ ...state, status: "speaking" });
}

export function stopSpeech(): void {
  current = null;
  if (speechAvailable()) window.speechSynthesis.cancel();
  if (state.status !== "idle") setState(IDLE);
}

/* ---------- what a block of the page says ---------- */

const BLOCK = new Set(["P", "LI", "H1", "H2", "H3", "H4", "DD", "SECTION", "DIV", "UL", "OL", "DL", "SMALL"]);
const SKIP = new Set(["BUTTON", "svg", "SCRIPT", "STYLE"]);

/**
 * The text of an element as a sentence sequence: blocks are set off with periods, a term is followed by a colon. Buttons,
 * icons and anything marked data-speech-skip are left out; data-speech replaces an element's text with a spoken
 * form (a case id spelled out, "card ending in 4821").
 */
export function spokenTextOf(root: Element): string {
  const parts: string[] = [];
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      parts.push(node.textContent ?? "");
      return;
    }
    if (!(node instanceof Element)) return;
    if (SKIP.has(node.tagName) || node.hasAttribute("data-speech-skip") || node.getAttribute("aria-hidden") === "true") return;
    if (BLOCK.has(node.tagName)) parts.push(". ");
    const spoken = node.getAttribute("data-speech");
    if (spoken !== null) parts.push(` ${spoken} `);
    else node.childNodes.forEach(walk);
    if (node.tagName === "DT") parts.push(": ");
    else if (BLOCK.has(node.tagName)) parts.push(". ");
  };
  walk(root);
  return parts
    .join("")
    .replace(/\s+/g, " ")
    .replace(/\s+([.,:;?!])/g, "$1")
    .replace(/([.:;?!])(?:\s*\.)+/g, "$1")
    .replace(/^[\s.]+/, "")
    .trim();
}

/** "CASE-294E237E628D" as "CASE, 2 9 4 E 2 3 7 E 6 2 8 D", so a listener can write it down. */
export function spellId(id: string): string {
  const [prefix, ...rest] = id.split("-");
  const body = rest.join("");
  if (!body) return id.split("").join(" ");
  return `${prefix}, ${body.split("").join(" ")}`;
}

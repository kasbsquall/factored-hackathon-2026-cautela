"use client";

import { useRef } from "react";
import { Pause, Play, SpeakerHigh, Stop } from "@phosphor-icons/react";
import type { CustomerCopy, UiLang } from "@/lib/i18n/customer";
import { pauseSpeech, resumeSpeech, speak, stopSpeech, useCanSpeak, useSpeechState } from "@/lib/speech";
import styles from "./read-aloud.module.css";

interface Props {
  /** Identifies this control, so only the one that started the utterance shows pause and stop. */
  id: string;
  /** Read at press time, so the text is always what is on screen. */
  text: () => string;
  /** Language of the text (the conversation language for messages, the label language for the receipt). */
  lang: UiLang;
  copy: CustomerCopy;
  /** Accessible name of the idle button ("Listen to this message"); the visible label stays short. */
  label: string;
  className?: string;
}

/**
 * Listen, then pause or resume, and stop. The main button keeps its place while it changes, so keyboard focus stays
 * on it. Without an on-device voice for the language the control is not rendered at all.
 */
export function ReadAloud({ id, text, lang, copy, label, className }: Props) {
  const canSpeak = useCanSpeak(lang);
  const speech = useSpeechState();
  const mainRef = useRef<HTMLButtonElement>(null);
  if (!canSpeak) return null;
  const mine = speech.id === id ? speech.status : "idle";

  function press() {
    if (mine === "speaking") pauseSpeech();
    else if (mine === "paused") resumeSpeech();
    else speak(id, text(), lang);
  }

  // The stop button disappears once pressed: focus goes back to the main button instead of the page.
  function stop() {
    stopSpeech();
    mainRef.current?.focus();
  }

  const [Icon, visible, name] = mine === "speaking" ? [Pause, copy.pause, copy.pause]
    : mine === "paused" ? [Play, copy.resume, copy.resume]
      : [SpeakerHigh, copy.listen, label];

  return (
    <span className={`${styles.group} ${className ?? ""}`} data-speech-skip>
      <button type="button" ref={mainRef} className={styles.btn} onClick={press} aria-label={name} data-state={mine}>
        <Icon aria-hidden />
        <span aria-hidden>{visible}</span>
      </button>
      {mine !== "idle" ? (
        <button type="button" className={styles.btn} onClick={stop}>
          <Stop aria-hidden />
          <span>{copy.stop}</span>
        </button>
      ) : null}
    </span>
  );
}

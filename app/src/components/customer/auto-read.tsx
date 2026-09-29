"use client";

import { useCallback, useId, useState } from "react";
import { SpeakerHigh, SpeakerSlash } from "@phosphor-icons/react";
import type { Language } from "@/lib/api/types";
import type { CustomerCopy } from "@/lib/i18n/customer";
import { stopSpeech, useCanSpeak } from "@/lib/speech";
import styles from "./auto-read.module.css";

const KEY = "cautela.read-aloud";

function stored(): boolean {
  try {
    return typeof window !== "undefined" && window.localStorage.getItem(KEY) === "on";
  } catch {
    return false;
  }
}

/**
 * "Read replies aloud", remembered by this browser. Storage can be missing or blocked: then it lasts for the page.
 * Read on first render (the conversation only renders in the browser, after login), so the greeting is read too.
 */
export function useAutoRead(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(stored);
  const change = useCallback((next: boolean) => {
    setOn(next);
    if (!next) stopSpeech();
    try {
      window.localStorage.setItem(KEY, next ? "on" : "off");
    } catch {
      // Only a convenience: without storage the choice lasts for this page.
    }
  }, []);
  return [on, change];
}

interface Props {
  on: boolean;
  onChange: (on: boolean) => void;
  /** Conversation language: the replies are read in it. */
  lang: Language;
  /** Labels (conversation language, or English in the reviewer view). */
  copy: CustomerCopy;
}

/** The chat header: a switch that reads each new reply aloud, and why it is off when the device has no voice. */
export function AutoReadBar({ on, onChange, lang, copy }: Props) {
  const noteId = useId();
  const canSpeak = useCanSpeak(lang);
  const active = on && canSpeak;
  return (
    <div className={styles.bar}>
      <button type="button" role="switch" className={styles.switch} aria-checked={active} aria-disabled={!canSpeak || undefined}
        aria-describedby={noteId} onClick={canSpeak ? () => onChange(!on) : undefined}>
        {active ? <SpeakerHigh aria-hidden /> : <SpeakerSlash aria-hidden />}
        <span>{copy.autoRead}</span>
        <span className={styles.track} aria-hidden><span className={styles.knob} /></span>
      </button>
      <p id={noteId} className={styles.note}>{canSpeak ? copy.autoReadNote : copy.noVoice(copy.languageName[lang])}</p>
    </div>
  );
}

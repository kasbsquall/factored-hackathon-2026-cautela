"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "@phosphor-icons/react";
import type { CustomerCopy } from "@/lib/i18n/customer";
import { spellId } from "@/lib/speech";
import styles from "./case-number.module.css";
import tool from "./read-aloud.module.css";

const COPIED_MS = 2000;

/** The clipboard API first; the older copy command where it is missing or blocked (http, some in-app browsers). */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Denied or unavailable: try the fallback below.
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

type CopyState = "idle" | "copied" | "failed";

/** The case number, large enough to write down, with copy and read-aloud next to it. */
export function CaseNumber({ id, copy, children }: { id: string; copy: CustomerCopy; children?: React.ReactNode }) {
  const [state, setState] = useState<CopyState>("idle");
  const idRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (state !== "copied") return;
    const timer = window.setTimeout(() => setState("idle"), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [state]);

  async function press() {
    const ok = await copyText(id);
    setState(ok ? "copied" : "failed");
    // Nothing reached the clipboard: select the number so the customer can copy it by hand.
    if (!ok && idRef.current) {
      const range = document.createRange();
      range.selectNodeContents(idRef.current);
      window.getSelection()?.removeAllRanges();
      window.getSelection()?.addRange(range);
    }
  }

  return (
    <div className={styles.block}>
      <span className="eyebrow" data-speech={`${copy.caseNumber}:`}>{copy.caseNumber}</span>
      <span ref={idRef} className={`${styles.id} mono`} data-speech={spellId(id)}>{id}</span>
      <div className={styles.actions} data-speech-skip>
        <button type="button" className={tool.btn} onClick={press} data-copy={state}>
          {state === "copied" ? <Check aria-hidden /> : <Copy aria-hidden />}
          <span>{state === "copied" ? copy.copied : copy.copyCase}</span>
        </button>
        {children}
      </div>
      <p className={state === "failed" ? styles.failed : "sr-only"} role="status" data-speech-skip>
        {state === "copied" ? copy.copiedStatus : state === "failed" ? copy.copyFailed : ""}
      </p>
    </div>
  );
}

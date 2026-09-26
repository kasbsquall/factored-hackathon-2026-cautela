"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, ChatCircleText, UserSwitch } from "@phosphor-icons/react";
import type { Language } from "@/lib/api/types";
import type { CustomerCopy } from "@/lib/i18n/customer";
import { Button } from "@/components/ui/button";
import type { Auth } from "./customer-app";
import { stageOf, type Entry } from "./flow-types";
import { useTurnFlow } from "./use-turn-flow";
import { EntryView } from "./entry-view";
import styles from "./conversation.module.css";

interface Props {
  auth: Auth;
  copy: CustomerCopy;
  lang: Language;
  onSessionEnd: () => void;
  onStage: (stage: number) => void;
}

const INTERACTIVE = new Set<Entry["kind"]>(["options", "recognize", "confirm"]);
/** Cards that take focus when they arrive while the customer is working inside the log. */
const FOCUS_ON_ARRIVAL = new Set<Entry["kind"]>([...INTERACTIVE, "receipt", "error"]);

/** A card the customer moved past (a newer message exists) can no longer be answered. */
function freeze(entry: Entry): Entry {
  if (entry.kind === "confirm" && entry.state === "pending") return { ...entry, state: "cancelled" };
  if (entry.kind === "recognize" && entry.state === "pending") return { ...entry, state: "cancelled" };
  return entry;
}

function announcement(entries: Entry[], copy: CustomerCopy): string {
  const last = entries[entries.length - 1];
  if (last?.kind === "thinking") return copy.thinking;
  const said = [...entries].reverse().find((e) => e.kind === "system");
  return said?.kind === "system" ? said.say(copy) : "";
}

function scrollBehavior(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

/** The service drives the conversation (live api/ or the in-browser mock); this view renders its turns. */
export function Conversation({ auth, copy, lang, onSessionEnd, onStage }: Props) {
  const flow = useTurnFlow({ token: auth.token, language: lang, copy, onSessionEnd });
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const logRef = useRef<HTMLOListElement>(null);
  const { entries } = flow;
  const started = entries.some((e) => e.kind === "user");
  const lastUser = entries.findLastIndex((e) => e.kind === "user");
  const last = entries[entries.length - 1];
  const handedOff = entries.some((e) => e.kind === "receipt" && Boolean(e.turn.handoff_id));
  const suggestion = auth.identity?.messages?.[lang]?.[0] ?? copy.suggestion;
  const finished = last?.kind === "receipt";

  useEffect(() => {
    onStage(stageOf(entries));
    endRef.current?.scrollIntoView({ block: "nearest", behavior: scrollBehavior() });
  }, [entries, onStage]);

  // Keyboard and screen reader users who answered a card continue on the card that answered them.
  const lastId = last?.id;
  const lastKind = last?.kind;
  useEffect(() => {
    const log = logRef.current;
    if (!log || !lastId || !lastKind || !FOCUS_ON_ARRIVAL.has(lastKind) || !log.contains(document.activeElement)) return;
    log.querySelector<HTMLElement>(`[data-entry="${lastId}"]`)?.focus({ preventScroll: true });
  }, [lastId, lastKind]);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || flow.busy) return;
    setDraft("");
    flow.send(text);
  }

  return (
    <div className={styles.wrap}>
      <p className="sr-only" role="status" aria-live="polite">{announcement(entries, copy)}</p>
      <ol className={styles.log} ref={logRef}>
        {entries.map((entry, i) => {
          const stale = i < lastUser && INTERACTIVE.has(entry.kind);
          return (
            <li key={entry.id} data-entry={entry.id} tabIndex={-1} className={`${styles.item} ${styles[entry.kind] ?? ""} rise`}>
              <EntryView entry={stale ? freeze(entry) : entry} isLast={i === entries.length - 1} flow={stale ? { ...flow, busy: true } : flow}
                copy={copy} lang={lang} canAskHuman={!handedOff} />
            </li>
          );
        })}
      </ol>
      <div ref={endRef} />

      {finished ? (
        <div className={styles.after}>
          <Button variant="secondary" onClick={flow.restart}>{copy.restart}</Button>
        </div>
      ) : null}

      <form className={styles.composer} onSubmit={submit}>
        {!started ? (
          <div className={styles.suggest}>
            <span className="eyebrow">{copy.suggestionLabel}</span>
            <button type="button" className={styles.chip} disabled={flow.busy} onClick={() => flow.send(suggestion)}>
              <ChatCircleText aria-hidden />
              <span>{suggestion}</span>
            </button>
          </div>
        ) : null}
        <div className={styles.inputRow}>
          <label htmlFor="composer" className="sr-only">{copy.composerLabel}</label>
          <input
            id="composer"
            className={styles.input}
            placeholder={copy.composerPlaceholder}
            value={draft}
            maxLength={1000}
            autoComplete="off"
            onChange={(e) => setDraft(e.target.value)}
          />
          <button type="submit" className={styles.send} disabled={!draft.trim() || flow.busy} aria-label={copy.send}>
            <ArrowUp aria-hidden />
          </button>
        </div>
        {!handedOff ? (
          <Button variant="ghost" size="sm" icon={<UserSwitch aria-hidden />} onClick={flow.askHuman} disabled={flow.busy}>
            {copy.askHuman}
          </Button>
        ) : null}
      </form>
    </div>
  );
}

"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, ChatCircleText, CheckCircle, UserSwitch } from "@phosphor-icons/react";
import type { Language, TurnStage } from "@/lib/api/types";
import type { CustomerCopy, UiLang } from "@/lib/i18n/customer";
import { Button } from "@/components/ui/button";
import type { Auth, TrailState } from "./customer-app";
import { stageOf, type Entry } from "./flow-types";
import { useTurnFlow } from "./use-turn-flow";
import { EntryView } from "./entry-view";
import styles from "./conversation.module.css";

interface Props {
  auth: Auth;
  /** Labels: the conversation language, or English in the reviewer view. */
  copy: CustomerCopy;
  /** Conversation language copy: the greeting and every text the customer sends. */
  talk: CustomerCopy;
  lang: Language;
  ui: UiLang;
  /** The service's demo clock, or null: claim deadlines are counted from its date. */
  demoClock: string | null;
  onSessionEnd: () => void;
  onStage: (stage: number) => void;
  onTrail: (trail: TrailState) => void;
}

const INTERACTIVE = new Set<Entry["kind"]>(["options", "recognize", "confirm"]);
/** Cards that take focus when they arrive while the customer is working inside the log. */
const FOCUS_ON_ARRIVAL = new Set<Entry["kind"]>([...INTERACTIVE, "receipt", "error"]);
/** Stages after which the service answers any message with "this conversation ended" (agent/orchestrator/state.py). */
const CLOSED_STAGES = new Set<TurnStage>(["resolved", "recognized", "abstained", "closed"]);

/** A card the customer moved past (a newer message exists) can no longer be answered. */
function freeze(entry: Entry): Entry {
  if (entry.kind === "confirm" && entry.state === "pending") return { ...entry, state: "cancelled" };
  if (entry.kind === "recognize" && entry.state === "pending") return { ...entry, state: "cancelled" };
  return entry;
}

/** What the live region reads, and its language: "thinking" is a label (UI language), a reply is conversation text. */
function announcement(entries: Entry[], copy: CustomerCopy, talk: CustomerCopy, ui: UiLang, lang: Language): { text: string; lang: string } {
  const last = entries[entries.length - 1];
  if (last?.kind === "thinking") return { text: copy.thinking, lang: ui };
  const said = [...entries].reverse().find((e) => e.kind === "system");
  return { text: said?.kind === "system" ? said.say(talk) : "", lang };
}

function scrollBehavior(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

/** The service drives the conversation (live api/ or the in-browser mock); this view renders its turns. */
export function Conversation({ auth, copy, talk, lang, ui, demoClock, onSessionEnd, onStage, onTrail }: Props) {
  const flow = useTurnFlow({ token: auth.token, language: lang, copy: talk, onSessionEnd });
  const [draft, setDraft] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const logRef = useRef<HTMLOListElement>(null);
  const composerRef = useRef<HTMLFormElement>(null);
  const { entries } = flow;
  const started = entries.some((e) => e.kind === "user");
  const lastUser = entries.findLastIndex((e) => e.kind === "user");
  const last = entries[entries.length - 1];
  const stage = flow.turns[flow.turns.length - 1]?.turn.stage;
  // After a handoff the service adds new requests to the case; after any other ending it only says the conversation ended.
  const handedOff = stage === "handed_off" || entries.some((e) => e.kind === "receipt" && Boolean(e.turn.handoff_id));
  const closed = !handedOff && stage !== undefined && CLOSED_STAGES.has(stage);
  const suggestion = auth.identity?.messages?.[lang]?.[0] ?? talk.suggestion;
  const spoken = announcement(entries, copy, talk, ui, lang);

  const { turns, pending, failed } = flow;
  useEffect(() => onTrail({ turns, pending, failed }), [turns, pending, failed, onTrail]);

  // The sticky composer covers the bottom of the log: its height is the margin the latest card is scrolled clear of.
  useEffect(() => {
    const wrap = wrapRef.current;
    const composer = composerRef.current;
    if (!wrap || !composer || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => wrap.style.setProperty("--composer-h", `${composer.offsetHeight}px`));
    observer.observe(composer);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    onStage(stageOf(entries));
    endRef.current?.scrollIntoView({ block: "nearest", behavior: scrollBehavior() });
  }, [entries, closed, handedOff, onStage]);

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
    <div className={styles.wrap} ref={wrapRef}>
      <p className="sr-only" role="status" aria-live="polite" lang={spoken.lang}>{spoken.text}</p>
      <ol className={styles.log} ref={logRef}>
        {entries.map((entry, i) => {
          const stale = i < lastUser && INTERACTIVE.has(entry.kind);
          return (
            <li key={entry.id} data-entry={entry.id} tabIndex={-1} className={`${styles.item} ${styles[entry.kind] ?? ""} rise`}>
              <EntryView entry={stale ? freeze(entry) : entry} isLast={i === entries.length - 1} flow={stale ? { ...flow, busy: true } : flow}
                copy={copy} talk={talk} lang={lang} ui={ui} demoClock={demoClock} canAskHuman={!handedOff && !closed} />
            </li>
          );
        })}
      </ol>
      <div ref={endRef} className={styles.end} />

      <form className={styles.composer} onSubmit={submit} ref={composerRef}>
        {closed || handedOff ? (
          <p className={styles.state}>
            {closed ? <CheckCircle aria-hidden /> : <UserSwitch aria-hidden />}
            <span>{closed ? copy.closedNote : copy.handedOffNote}</span>
          </p>
        ) : null}
        {!started ? (
          <div className={styles.suggest}>
            <span className="eyebrow">{copy.suggestionLabel}</span>
            <button type="button" className={styles.chip} disabled={flow.busy} onClick={() => flow.send(suggestion)}>
              <ChatCircleText aria-hidden />
              <span lang={lang}>{suggestion}</span>
            </button>
          </div>
        ) : null}
        {!closed ? (
          <div className={styles.inputRow}>
            <label htmlFor="composer" className="sr-only">{copy.composerLabel}</label>
            <input
              id="composer"
              lang={lang}
              className={styles.input}
              placeholder={handedOff ? copy.handedOffPlaceholder : copy.composerPlaceholder}
              value={draft}
              maxLength={1000}
              autoComplete="off"
              onChange={(e) => setDraft(e.target.value)}
            />
            <button type="submit" className={styles.send} disabled={!draft.trim() || flow.busy} aria-label={copy.send}>
              <ArrowUp aria-hidden />
            </button>
          </div>
        ) : null}
        {closed || handedOff ? (
          <Button variant={closed ? "primary" : "secondary"} size="sm" onClick={flow.restart} disabled={flow.busy}>{copy.restart}</Button>
        ) : (
          <Button variant="ghost" size="sm" icon={<UserSwitch aria-hidden />} onClick={flow.askHuman} disabled={flow.busy}>
            {copy.askHuman}
          </Button>
        )}
      </form>
    </div>
  );
}

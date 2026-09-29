"use client";

import { useId, useRef, useState } from "react";
import { ArrowClockwise, Translate } from "@phosphor-icons/react";
import { getApi } from "@/lib/api";
import { ApiError, type Translation } from "@/lib/api/client";
import type { Language } from "@/lib/api/types";
import { CUSTOMER_COPY } from "@/lib/i18n/customer";
import { translateTemplateReply } from "@/lib/i18n/reply-templates";
import type { Entry } from "./flow-types";
import styles from "./message-translation.module.css";

type Message = Extract<Entry, { kind: "user" | "system" }>;

/** Where an English version comes from. Only "machine" involves a language model. */
export type EnglishSource =
  | { kind: "template"; text: string; template: string }
  | { kind: "copy"; text: string }
  | { kind: "machine" };

/** Customer texts the app itself sends (buttons and the example chip), which have an English version in the UI copy. */
const SENT_COPY = ["none", "askHumanMessage", "suggestion"] as const;

/**
 * How to get this message in English, without calling anything: the app's own copy, or the reply template the turn
 * trail names. Anything else (what the customer typed, a reply the model worded) needs machine translation. An
 * option pick shows the charge label, which is data and needs no translation (null).
 */
export function englishSource(entry: Message, talk: Language): EnglishSource | null {
  if (entry.kind === "user") {
    if (entry.option !== undefined) return null;
    const key = SENT_COPY.find((k) => CUSTOMER_COPY[entry.language][k] === entry.text);
    return key ? { kind: "copy", text: CUSTOMER_COPY.en[key] } : { kind: "machine" };
  }
  if (!entry.reply) return { kind: "copy", text: entry.say(CUSTOMER_COPY.en) };
  if (entry.reply.source === "template" && entry.reply.template) {
    const text = translateTemplateReply(entry.say(CUSTOMER_COPY[talk]), entry.reply.template, entry.reply.language);
    if (text) return { kind: "template", text, template: entry.reply.template };
  }
  return { kind: "machine" };
}

type State =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; result: Translation }
  | { status: "error"; reason: "unavailable" | "not_found" | "network" | "other" };

/** Session-wide cache, so hiding and showing again (or switching the reviewer view) never asks twice. */
const cache = new Map<string, Translation>();

/** Forget every translation: called when the customer logs out or the session ends, so no text outlives it. */
export function clearTranslationCache(): void {
  cache.clear();
}

function reasonOf(err: unknown): Extract<State, { status: "error" }>["reason"] {
  if (!(err instanceof ApiError)) return "other";
  if (err.code === "translation_unavailable") return "unavailable";
  if (err.code === "not_found") return "not_found";
  if (err.code === "network") return "network";
  return "other";
}

const ERROR_TEXT: Record<Extract<State, { status: "error" }>["reason"], string> = {
  unavailable: "No English version: the service has no language model available right now, so it cannot translate free text.",
  not_found: "No English version yet: the service has not recorded this message in the conversation.",
  network: "The service could not be reached. Nothing was translated.",
  other: "The translation failed. Nothing was translated.",
};

function machineNote(t: Translation): React.ReactNode {
  if (t.method === "authored") return "English written for this test message. Mock mode has no language model.";
  // Provider and model names never break at a hyphen ("gpt-4o-mini").
  const parts: React.ReactNode[] = ["Machine translation", ...[t.provider, t.model].filter(Boolean).map((v) => <span key={v} className={styles.nowrap}>{v}</span>)];
  if (t.masked) parts.push("personal data masked before it reached the model");
  return parts.flatMap((p, i) => (i ? [" · ", p] : [p]));
}

interface Props {
  entry: Message;
  /** Conversation language: the message is in this language whatever the reviewer view shows. */
  talk: Language;
  translate: (role: "customer" | "assistant", text: string) => Promise<Translation>;
  conversationId: string | null;
  /** Other tools for the same message (read aloud), on the same row as the toggle. */
  leading?: React.ReactNode;
}

/** "Show English translation" under one message, for reviewers. The message itself is never replaced. */
export function MessageTranslation({ entry, talk, translate, conversationId, leading }: Props) {
  const regionId = useId();
  const source = englishSource(entry, talk);
  // A service reply is sent as the service recorded it (options included); the service checks it against the conversation.
  const text = entry.kind === "user" ? entry.text : entry.reply?.raw ?? entry.say(CUSTOMER_COPY[talk]);
  const role = entry.kind === "user" ? "customer" : "assistant";
  const key = `${conversationId ?? ""}|${role}|${text}`;
  const [open, setOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const [state, setState] = useState<State>(() => {
    const hit = cache.get(key);
    return hit ? { status: "done", result: hit } : { status: "idle" };
  });

  if (!source) return leading ? <div className={`${styles.wrap} ${entry.kind === "user" ? styles.end : ""}`}><div className={styles.tools}>{leading}</div></div> : null;

  function load() {
    // The retry button leaves the page while loading: keep keyboard focus on the toggle instead of losing it.
    if (toggleRef.current && document.activeElement !== toggleRef.current && toggleRef.current.parentElement?.contains(document.activeElement)) {
      toggleRef.current.focus();
    }
    setState({ status: "loading" });
    translate(role, text)
      .then((result) => {
        cache.set(key, result);
        setState({ status: "done", result });
      })
      .catch((err: unknown) => setState({ status: "error", reason: reasonOf(err) }));
  }

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && source?.kind === "machine" && (state.status === "idle" || state.status === "error")) load();
  }

  return (
    <div className={`${styles.wrap} ${entry.kind === "user" ? styles.end : ""}`}>
      <div className={styles.tools}>
        {leading}
        <button type="button" ref={toggleRef} className={styles.toggle} aria-expanded={open} aria-controls={regionId} onClick={toggle} lang="en">
          <Translate aria-hidden />
          <span>{open ? "Hide English translation" : "Show English translation"}</span>
        </button>
      </div>
      {open ? (
        <div id={regionId} className={styles.panel} aria-live="polite" lang="en">
          {source.kind !== "machine" ? (
            <>
              <p className={styles.text}>{source.text}</p>
              <p className={styles.note}>
                {source.kind === "template"
                  ? `Deterministic: the English version of the fixed reply template "${source.template}", no model involved.`
                  : "The app's own English text for this message, no model involved."}
              </p>
            </>
          ) : state.status === "loading" || state.status === "idle" ? (
            <div className={styles.loading} aria-busy="true">
              <span className="sr-only">Translating</span>
              <span className={`skeleton ${styles.line}`} />
              <span className={`skeleton ${styles.line} ${styles.short}`} />
            </div>
          ) : state.status === "done" ? (
            <>
              <p className={styles.text}>{state.result.text}</p>
              <p className={styles.note}>{machineNote(state.result)}</p>
            </>
          ) : (
            <div className={styles.error}>
              <p className={styles.note}>
                {state.reason === "unavailable" && getApi().mode === "mock"
                  ? "No English version: mock mode has no language model, so only the suggested test messages have one."
                  : ERROR_TEXT[state.reason]}
              </p>
              {state.reason === "network" || state.reason === "other" ? (
                <button type="button" className={styles.retry} onClick={load}>
                  <ArrowClockwise aria-hidden />
                  <span>Try again</span>
                </button>
              ) : null}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

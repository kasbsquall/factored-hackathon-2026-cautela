"use client";

import { UserSwitch, WarningOctagon } from "@phosphor-icons/react";
import type { Language } from "@/lib/api/types";
import type { CustomerCopy, UiLang } from "@/lib/i18n/customer";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { DialMark } from "@/components/brand/dial-mark";
import type { Entry, Flow } from "./flow-types";
import { ConfirmCard } from "./confirm-card";
import { OptionList } from "./option-list";
import { RecognizeCard } from "./recognize-card";
import { OutcomeReceipt } from "./outcome-receipt";
import { MessageTranslation } from "./message-translation";
import { ReadAloud } from "./read-aloud";
import styles from "./conversation.module.css";

interface Props {
  entry: Entry;
  isLast: boolean;
  flow: Flow;
  /** Labels (conversation language, or English in the reviewer view). */
  copy: CustomerCopy;
  /** Conversation language copy, for the greeting. */
  talk: CustomerCopy;
  /** Conversation language: messages are always shown in it. */
  lang: Language;
  /** Label and number format language of the cards. */
  ui: UiLang;
  /** The service's demo clock, or null: claim deadlines are counted from its date. */
  demoClock: string | null;
  canAskHuman: boolean;
  /** expires_at of the session the one-time code opened, for the receipt's identity check. */
  sessionUntil: string | null;
}

export function EntryView({ entry, isLast, flow, copy, talk, lang, ui, demoClock, canAskHuman, sessionUntil }: Props) {
  // Every assistant message can be read aloud, in the conversation language it is written in.
  const listen = entry.kind === "system"
    ? <ReadAloud id={entry.id} text={() => entry.say(talk)} lang={lang} copy={copy} label={copy.listenMessage} />
    : null;
  const translation = entry.kind === "user" || entry.kind === "system"
    ? <MessageTranslation entry={entry} talk={lang} translate={flow.translate} conversationId={flow.conversationId} leading={listen} />
    : null;
  switch (entry.kind) {
    case "user":
      return (
        <>
          <p className={`${styles.msg} ${styles.me}`}><span className="sr-only">{copy.you}: </span><span lang={lang}>{entry.text}</span></p>
          {translation}
        </>
      );
    case "system":
      return (
        <>
          <p className={`${styles.msg} ${styles.sys}`}><span className="sr-only">{copy.assistant}: </span><span lang={lang}>{entry.say(talk)}</span></p>
          {translation}
        </>
      );
    case "thinking":
      return (
        <div className={styles.thinking}>
          <DialMark size={20} turning />
          <span>{copy.thinking}</span>
          <span className={`skeleton ${styles.thinkLine}`} />
        </div>
      );
    case "options":
      return <OptionList entry={entry} copy={copy} lang={ui} disabled={flow.busy} onPick={(o) => flow.pickOption(entry.id, o)} />;
    case "recognize":
      return <RecognizeCard entry={entry} copy={copy} lang={ui} demoClock={demoClock} onAnswer={(recognized) => flow.recognize(entry, recognized)} />;
    case "confirm":
      return <ConfirmCard entry={entry} copy={copy} lang={ui} demoClock={demoClock} onConfirm={() => flow.confirm(entry)} onCancel={() => flow.cancel(entry)} />;
    case "receipt":
      return <OutcomeReceipt entry={entry} copy={copy} lang={ui} demoClock={demoClock} turns={flow.turns.map((t) => t.turn)}
        sessionUntil={sessionUntil} onAskHuman={flow.askHuman} busy={flow.busy} />;
    case "error":
      return (
        <Notice tone="error" icon={<WarningOctagon aria-hidden />} title={entry.title?.(copy) ?? copy.loadFailTitle}
          actions={isLast ? (
            <>
              <Button variant="secondary" size="sm" onClick={entry.retry}>{entry.retryLabel?.(copy) ?? copy.retry}</Button>
              {canAskHuman ? <Button variant="ghost" size="sm" icon={<UserSwitch aria-hidden />} onClick={flow.askHuman}>{copy.askHuman}</Button> : null}
            </>
          ) : undefined}>
          {entry.body?.(copy) ?? copy.loadFailBody}
        </Notice>
      );
  }
}

"use client";

import { UserSwitch, WarningOctagon } from "@phosphor-icons/react";
import type { Language } from "@/lib/api/types";
import type { CustomerCopy } from "@/lib/i18n/customer";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { DialMark } from "@/components/brand/dial-mark";
import type { Entry, Flow } from "./flow-types";
import { ConfirmCard } from "./confirm-card";
import { OptionList } from "./option-list";
import { OutcomeReceipt } from "./outcome-receipt";
import styles from "./conversation.module.css";

interface Props {
  entry: Entry;
  isLast: boolean;
  flow: Flow;
  copy: CustomerCopy;
  lang: Language;
  canAskHuman: boolean;
}

export function EntryView({ entry, isLast, flow, copy, lang, canAskHuman }: Props) {
  switch (entry.kind) {
    case "user":
      return <p className={`${styles.msg} ${styles.me}`}><span className="sr-only">{copy.you}: </span>{entry.text}</p>;
    case "system":
      return <p className={`${styles.msg} ${styles.sys}`}><span className="sr-only">{copy.assistant}: </span>{entry.say(copy)}</p>;
    case "thinking":
      return (
        <div className={styles.thinking}>
          <DialMark size={20} turning />
          <span>{copy.thinking}</span>
          <span className={`skeleton ${styles.thinkLine}`} />
        </div>
      );
    case "options":
      return <OptionList entry={entry} copy={copy} lang={lang} disabled={flow.busy} onPick={(o) => flow.pickOption(entry.id, o)} />;
    case "confirm":
      return <ConfirmCard entry={entry} copy={copy} lang={lang} onConfirm={() => flow.confirm(entry)} onCancel={() => flow.cancel(entry)} />;
    case "receipt":
      return <OutcomeReceipt entry={entry} copy={copy} lang={lang} />;
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

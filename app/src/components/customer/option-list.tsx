"use client";

import { CalendarBlank, Info, Question } from "@phosphor-icons/react";
import type { Language } from "@/lib/api/types";
import type { CustomerCopy } from "@/lib/i18n/customer";
import { labelAmount, parseTxLabel } from "@/lib/format";
import type { Entry } from "./flow-types";
import styles from "./option-list.module.css";

interface Props {
  entry: Extract<Entry, { kind: "options" }>;
  copy: CustomerCopy;
  lang: Language;
  disabled: boolean;
  onPick: (option: Extract<Entry, { kind: "options" }>["options"][number] | null) => void;
}


/** Live mode: the service ranks the options; the UI shows them all and never picks one for the customer. */
export function OptionList({ entry, copy, lang, disabled, onPick }: Props) {
  const locked = entry.pickedIndex !== undefined || disabled;
  return (
    <div className={styles.wrap}>
      <div className={styles.list} role="group" aria-label={copy.optionsTitle}>
        {entry.options.map((o, i) => {
          const parts = parseTxLabel(o.label);
          return (
            <button key={o.index} type="button" className={`${styles.cand} rise-row`} style={{ "--i": Math.min(i, 7) } as React.CSSProperties}
              aria-pressed={entry.pickedIndex === o.index} aria-disabled={locked || undefined} onClick={locked ? undefined : () => onPick(o)}>
              <span className={styles.rank} aria-hidden>{o.index}</span>
              <span className="sr-only">{copy.optionN(o.index)}. </span>
              {parts ? (
                <>
                  <span className={`${styles.merchant} mono`}>{parts.who}</span>
                  <span className={`${styles.amount} mono`}>{labelAmount(parts, lang)}</span>
                  <span className={styles.when}><CalendarBlank aria-hidden />{parts.when}</span>
                </>
              ) : (
                <span className={`${styles.merchant} mono`}>{o.label}</span>
              )}
            </button>
          );
        })}
        <button type="button" className={`${styles.cand} ${styles.none} rise-row`} style={{ "--i": Math.min(entry.options.length, 7) } as React.CSSProperties}
          aria-pressed={entry.pickedIndex === null} aria-disabled={locked || undefined} onClick={locked ? undefined : () => onPick(null)}>
          <Question aria-hidden />
          <span>{copy.none}</span>
        </button>
      </div>
      <p className={styles.note}><Info aria-hidden /><span>{copy.optionsNote}</span></p>
    </div>
  );
}

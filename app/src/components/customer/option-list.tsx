"use client";

import { CalendarBlank, Info, Question } from "@phosphor-icons/react";
import type { OptionView } from "@/lib/api/types";
import type { CustomerCopy, UiLang } from "@/lib/i18n/customer";
import { labelAmount, labelDate, parseTxLabel } from "@/lib/format";
import type { Entry } from "./flow-types";
import { ChargeMeta, ReasonChips, chargeAmount } from "./charge-facts";
import styles from "./option-list.module.css";

interface Props {
  entry: Extract<Entry, { kind: "options" }>;
  copy: CustomerCopy;
  lang: UiLang;
  disabled: boolean;
  onPick: (option: OptionView | null) => void;
}

/** A candidate as the service sent it: structured charge data when present, else its label. */
function Candidate({ option, copy, lang }: { option: OptionView; copy: CustomerCopy; lang: UiLang }) {
  const charge = option.charge;
  if (charge) {
    return (
      <>
        <span className={`${styles.merchant} mono`}>{charge.merchant_name ?? copy.txType[charge.transaction_type ?? ""] ?? "?"}</span>
        <span className={`${styles.amount} mono`}>{chargeAmount(charge, lang)}</span>
        <span className={styles.detail}><ChargeMeta charge={charge} copy={copy} lang={lang} /></span>
        <span className={styles.detail}><ReasonChips reasons={option.reasons} copy={copy} lang={lang} /></span>
      </>
    );
  }
  const parts = parseTxLabel(option.label);
  if (!parts) return <span className={`${styles.merchant} mono`}>{option.label}</span>;
  return (
    <>
      <span className={`${styles.merchant} mono`}>{parts.who}</span>
      <span className={`${styles.amount} mono`}>{labelAmount(parts, lang)}</span>
      <span className={styles.when}><CalendarBlank aria-hidden />{labelDate(parts, lang)}</span>
    </>
  );
}

/** The service ranks the options; the UI shows them all, says why each matched, and never picks one itself. */
export function OptionList({ entry, copy, lang, disabled, onPick }: Props) {
  const locked = entry.pickedIndex !== undefined || disabled;
  return (
    <div className={styles.wrap}>
      <div className={styles.list} role="group" aria-label={copy.optionsTitle}>
        {entry.options.map((o, i) => (
          <button key={o.index} type="button" className={`${styles.cand} rise-row`} style={{ "--i": Math.min(i, 7) } as React.CSSProperties}
            aria-pressed={entry.pickedIndex === o.index} aria-disabled={locked || undefined} onClick={locked ? undefined : () => onPick(o)}>
            <span className={styles.rank} aria-hidden>{o.index}</span>
            <span className="sr-only">{copy.optionN(o.index)}. </span>
            <Candidate option={o} copy={copy} lang={lang} />
          </button>
        ))}
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

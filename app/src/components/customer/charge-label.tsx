import { CalendarBlank } from "@phosphor-icons/react";
import type { ChargeView } from "@/lib/api/types";
import type { CustomerCopy, UiLang } from "@/lib/i18n/customer";
import { labelAmount, labelDate, parseTxLabel } from "@/lib/format";
import { ChargeMeta, chargeAmount } from "./charge-facts";
import styles from "./confirm-card.module.css";

/** The charge from its structured tool data: merchant and amount on one line, when, how and where under them. */
export function ChargeBlock({ charge, copy, lang }: { charge: ChargeView; copy: CustomerCopy; lang: UiLang }) {
  return (
    <div className={styles.charge}>
      <span className="mono">{charge.merchant_name ?? "?"}</span>
      <span className={`${styles.chargeAmount} mono`}>{chargeAmount(charge, lang)}</span>
      <span className={styles.chargeWhen}><ChargeMeta charge={charge} copy={copy} lang={lang} /></span>
    </div>
  );
}

/** The charge a confirmation is about: merchant and amount on one line, the date under them. */
export function ChargeLabel({ label, lang }: { label: string; lang: UiLang }) {
  const parts = parseTxLabel(label);
  if (!parts) return <div className={styles.charge}><span className="mono">{label}</span></div>;
  return (
    <div className={styles.charge}>
      <span className="mono">{parts.who}</span>
      <span className={`${styles.chargeAmount} mono`}>{labelAmount(parts, lang)}</span>
      <span className={styles.chargeWhen}><CalendarBlank aria-hidden />{labelDate(parts, lang)}</span>
    </div>
  );
}

/** The same charge as one line of text, for receipt rows. Structured data wins over the label when present. */
export function ChargeLine({ label, charge, lang }: { label: string | null; charge: ChargeView | null; lang: UiLang }) {
  if (charge) {
    return (
      <span>
        {charge.merchant_name ?? "?"} · <span className="mono" style={{ whiteSpace: "nowrap" }}>{chargeAmount(charge, lang)}</span>
      </span>
    );
  }
  return label ? <ChargeText label={label} lang={lang} /> : null;
}

/** A service label as one line of text. */
export function ChargeText({ label, lang }: { label: string; lang: UiLang }) {
  const parts = parseTxLabel(label);
  if (!parts) return <span className="mono">{label}</span>;
  return (
    <span>
      {parts.who} · <span className="mono" style={{ whiteSpace: "nowrap" }}>{labelAmount(parts, lang)}</span> · <span className="num">{labelDate(parts, lang)}</span>
    </span>
  );
}

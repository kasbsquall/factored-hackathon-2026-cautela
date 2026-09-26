import { CalendarBlank } from "@phosphor-icons/react";
import type { Language } from "@/lib/api/types";
import { labelAmount, parseTxLabel } from "@/lib/format";
import styles from "./confirm-card.module.css";

/** The charge a confirmation is about: merchant and amount on one line, the date under them. */
export function ChargeLabel({ label, lang }: { label: string; lang: Language }) {
  const parts = parseTxLabel(label);
  if (!parts) return <div className={styles.charge}><span className="mono">{label}</span></div>;
  return (
    <div className={styles.charge}>
      <span className="mono">{parts.who}</span>
      <span className={`${styles.chargeAmount} mono`}>{labelAmount(parts, lang)}</span>
      <span className={styles.chargeWhen}><CalendarBlank aria-hidden />{parts.when}</span>
    </div>
  );
}

/** The same charge as one line of text, for receipt rows. */
export function ChargeText({ label, lang }: { label: string; lang: Language }) {
  const parts = parseTxLabel(label);
  if (!parts) return <span className="mono">{label}</span>;
  return (
    <span>
      {parts.who} · <span className="mono" style={{ whiteSpace: "nowrap" }}>{labelAmount(parts, lang)}</span> · <span className="num">{parts.when}</span>
    </span>
  );
}

import { ArrowsHorizontal, Asterisk } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import styles from "./business.module.css";

/** A ledger table that scrolls inside its own box; the hint shows only where it cannot fit. */
export function Ledger({ label, columns, children }: { label: string; columns: number; children: ReactNode }) {
  return (
    <>
      {columns > 3 ? (
        <p className={`${styles.swipe} ${styles.narrowOnly}`}><ArrowsHorizontal aria-hidden />Scroll sideways inside the table for all {columns} columns.</p>
      ) : null}
      <div className={styles.scroll} role="region" aria-label={label} tabIndex={0}>
        {children}
      </div>
    </>
  );
}

/** Marks a column whose values are the team's judgment, not a measurement. */
export function Judgment() {
  return (
    <span className={styles.judgment} title="Team judgment, not measured">
      <Asterisk aria-hidden />
      <span className="sr-only">(team judgment)</span>
    </span>
  );
}

export function Source({ children }: { children: ReactNode }) {
  return <p className={styles.source}>Source: <span className="mono">{children}</span></p>;
}

export function Assumption() {
  return <span className={styles.assume}>assumption</span>;
}

/** One figure: label, value with its unit, and an optional note. `tone` sets the lead or a muted "not defined". */
export function Figure({ label, value, unit, tone }: { label: string; value: string; unit?: string; tone?: "lead" | "muted" }) {
  return (
    <div className={styles.figure}>
      <dt>{label}</dt>
      <dd>
        <span className={`${styles.figValue} ${tone ? styles[tone] : ""}`}>{value}</span>
        {unit ? <span className={styles.figUnit}>{unit}</span> : null}
      </dd>
    </div>
  );
}

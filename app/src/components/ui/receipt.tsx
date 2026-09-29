"use client";

import { Prohibit, UserSwitch, WarningOctagon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import type { StatusKind } from "./status-chip";
import styles from "./receipt.module.css";

export interface ReceiptRow {
  label: string;
  value: ReactNode;
}

interface ReceiptProps {
  kind: StatusKind;
  title: string;
  rows: ReceiptRow[];
  foot?: ReactNode;
  children?: ReactNode;
  index?: number;
}

function Stamp() {
  return (
    <svg className={styles.stamp} viewBox="0 0 24 24" aria-hidden>
      <path d="M5 12.5l4.2 4.2L19 7" />
    </svg>
  );
}

const HEAD_ICON: Record<Exclude<StatusKind, "ok">, ReactNode> = {
  cf: <UserSwitch aria-hidden />,
  hu: <UserSwitch aria-hidden />,
  stop: <Prohibit aria-hidden />,
  bad: <WarningOctagon aria-hidden />,
};

/** Acting and abstaining get the same visual weight: same bezel, same rows, different head. */
export function Receipt({ kind, title, rows, foot, children, index = 0 }: ReceiptProps) {
  return (
    <section className={`${styles.bezel} rise`} style={{ "--i": index } as React.CSSProperties} aria-label={title}>
      <div className={`${styles.card} ${styles[kind]}`}>
        <h3 className={styles.head}>
          {kind === "ok" ? <Stamp /> : HEAD_ICON[kind]}
          {title}
        </h3>
        {children}
        <dl className={styles.rows}>
          {rows.map((r, i) => (
            <div key={`${r.label}-${i}`} className={styles.row}>
              <dt>{r.label}</dt>
              <dd>{r.value}</dd>
            </div>
          ))}
        </dl>
        {/* Technical references are for the audit view, not for reading aloud. */}
        {foot ? <div className={styles.foot} data-speech-skip>{foot}</div> : null}
      </div>
    </section>
  );
}

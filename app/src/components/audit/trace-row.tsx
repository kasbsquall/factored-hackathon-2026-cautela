"use client";

import { useState } from "react";
import { CaretDown, Warning } from "@phosphor-icons/react";
import type { AuditRecord } from "@/lib/api/types";
import { barOf, latency, outcomeKind, outcomeLabel, phaseOf, type TraceSummary } from "@/lib/audit";
import { shortHash } from "@/lib/format";
import { StatusChip } from "@/components/ui/status-chip";
import { PhaseIcon } from "./phase-icon";
import styles from "./trace-row.module.css";

interface Props {
  record: AuditRecord;
  summary: TraceSummary;
  index: number;
  broken: boolean;
}

function Outcome({ record: r }: { record: AuditRecord }) {
  const kind = outcomeKind(r.outcome);
  const label = outcomeLabel(r.outcome);
  const showCode = label.toLowerCase() !== r.outcome;
  return (
    <span className={styles.outcome}>
      {kind ? <StatusChip kind={kind} size="sm">{label}</StatusChip> : <span className={styles.plain}>{label}</span>}
      {showCode ? <span className={`${styles.code} mono`}>{r.outcome}</span> : null}
      {r.reason ? <span className={`${styles.reason} mono`}>{r.reason}</span> : null}
    </span>
  );
}

export function TraceRow({ record: r, summary, index, broken }: Props) {
  const [open, setOpen] = useState(false);
  const phase = phaseOf(r);
  const bar = barOf(r, summary);
  const detailId = `rec-${r.seq}`;
  const time = new Date(r.ts).toISOString().slice(11, 23);

  return (
    <li className={`${styles.item} ${broken ? styles.broken : ""} rise-row`} style={{ "--i": Math.min(index, 7) } as React.CSSProperties}>
      <button type="button" className={styles.row} aria-expanded={open} aria-controls={detailId} onClick={() => setOpen((o) => !o)}>
        <span className={`${styles.seq} mono`}>{r.seq}</span>
        <span className={styles.step}>
          <span className={styles.phase} data-phase={phase}><PhaseIcon phase={phase} /></span>
          <span className="mono">{r.step}</span>
          {r.tool ? <span className={`${styles.tool} mono`}>{r.tool}</span> : null}
        </span>
        <Outcome record={r} />
        <span className={`${styles.rules} mono`}>{r.rule_ids.map((id) => <span key={id}>{id}</span>)}</span>
        <span className={styles.timing}>
          <span className={styles.track} aria-hidden>
            <span className={styles.bar} data-phase={phase} style={{ "--o": bar.offset, "--w": bar.width, "--i": Math.min(index, 7) } as React.CSSProperties} />
          </span>
          <span className={`${styles.ms} mono`}>{latency(r.latency_ms)}{r.attempts > 1 ? ` · ${r.attempts} attempts` : ""}</span>
        </span>
        <span className={`${styles.hash} mono`}>
          {broken ? (
            <>
              <Warning aria-hidden />
              <span className="sr-only">The chain breaks at this record.</span>
            </>
          ) : null}
          <span className={styles.hashText}>{shortHash(r.record_hash)}</span>
        </span>
        <CaretDown className={styles.caret} aria-hidden />
      </button>
      <dl className={styles.detail} id={detailId} hidden={!open}>
        <div><dt>Time</dt><dd className="mono">{time} UTC</dd></div>
        <div><dt>Customer ref</dt><dd className="mono">{r.customer_ref ?? "none"}</dd></div>
        <div className={styles.wide}><dt>Masked arguments</dt><dd><pre className="mono">{JSON.stringify(r.masked_args, null, 2)}</pre></dd></div>
        <div className={styles.wide}><dt>Arguments hash</dt><dd className="mono">{r.args_hash ?? "no arguments"}</dd></div>
        <div className={styles.wide}><dt>Previous hash</dt><dd className="mono">{r.prev_hash}</dd></div>
        <div className={styles.wide}><dt>Record hash</dt><dd className="mono">{r.record_hash}</dd></div>
      </dl>
    </li>
  );
}

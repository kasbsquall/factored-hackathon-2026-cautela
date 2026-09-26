"use client";

import Link from "next/link";
import { ArrowRight, ListChecks, WarningOctagon } from "@phosphor-icons/react";
import { getApi } from "@/lib/api";
import { REASON } from "@/lib/labels";
import type { TransferReasonCode } from "@/lib/api/types";
import { useResource } from "@/lib/use-resource";
import { Notice } from "@/components/ui/notice";
import { Button } from "@/components/ui/button";
import { StatusChip } from "@/components/ui/status-chip";
import styles from "./trace-list.module.css";

export function TraceList() {
  const traces = useResource("traces", () => getApi().listTraces());
  return (
    <div className={styles.page}>
      <header className={`${styles.head} rise`}>
        <h1 className={styles.title}>Audit trail</h1>
        <p className={styles.lede}>
          One trace per conversation. Each record is chained to the previous one by its hash, so an edited or deleted line shows up as a break.
        </p>
      </header>
      {traces.status === "loading" ? (
        <div className={styles.list} aria-busy="true" aria-label="Loading traces">
          {[0, 1, 2, 3, 4].map((i) => <span key={i} className="skeleton" style={{ height: 56 }} />)}
        </div>
      ) : traces.status === "error" ? (
        <Notice tone="error" icon={<WarningOctagon aria-hidden />} title="The traces could not be loaded"
          actions={<Button variant="secondary" size="sm" onClick={traces.reload}>Retry</Button>}>
          Nothing was changed. Retry in a moment.
        </Notice>
      ) : traces.data.length === 0 ? (
        <Notice tone="empty" icon={<ListChecks aria-hidden />} title="No traces yet">
          Run a customer conversation and its trace appears here.
        </Notice>
      ) : (
        <ul className={styles.list}>
          {traces.data.map((t, i) => {
            const reason = REASON[t.label as TransferReasonCode];
            return (
              <li key={t.trace_id} className="rise-row" style={{ "--i": Math.min(i, 7) } as React.CSSProperties}>
                <Link href={`/audit/${t.trace_id}`} className={styles.row}>
                  <span className={`${styles.id} mono`}>{t.trace_id}</span>
                  <span className={styles.label}>
                    {reason ? <StatusChip kind={reason.kind} size="sm">{reason.label}</StatusChip> : t.label}
                  </span>
                  <ArrowRight aria-hidden />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

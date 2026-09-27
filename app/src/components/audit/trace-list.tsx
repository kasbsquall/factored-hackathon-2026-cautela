"use client";

import Link from "next/link";
import { ArrowRight, ListChecks, WarningOctagon } from "@phosphor-icons/react";
import { ApiError } from "@/lib/api";
import { REASON } from "@/lib/labels";
import type { TransferReasonCode } from "@/lib/api/types";
import { useResource } from "@/lib/use-resource";
import { Notice } from "@/components/ui/notice";
import { Button } from "@/components/ui/button";
import { StatusChip, type StatusKind } from "@/components/ui/status-chip";
import { LIVE, listConversations, type ConversationRow } from "./conversation-data";
import styles from "./trace-list.module.css";

/** Final stages as the audit index names them (agent/orchestrator/state.py FINAL_STAGES). */
const OUTCOME: Record<string, { label: string; kind: StatusKind }> = {
  resolved: { label: "Resolved, case opened", kind: "ok" },
  recognized: { label: "Customer recognized the charge", kind: "ok" },
  abstained: { label: "Ended without an action", kind: "cf" },
  closed: { label: "Customer declined the action", kind: "cf" },
};

function Outcome({ row }: { row: ConversationRow }) {
  if (row.mockLabel !== null) {
    const reason = REASON[row.mockLabel as TransferReasonCode];
    return reason ? <StatusChip kind={reason.kind} size="sm">{reason.label}</StatusChip> : <>{row.mockLabel}</>;
  }
  if (row.stage === "handed_off") {
    const reason = row.transferReason ? REASON[row.transferReason as TransferReasonCode] : undefined;
    return <StatusChip kind={reason?.kind ?? "hu"} size="sm">{reason?.label ?? "Handed to a person"}</StatusChip>;
  }
  const outcome = row.stage ? OUTCOME[row.stage] : undefined;
  if (outcome) return <StatusChip kind={outcome.kind} size="sm">{outcome.label}</StatusChip>;
  return <span className={styles.open}>In progress: <span className="mono">{row.stage}</span></span>;
}

const LEDE = LIVE
  ? "Every conversation since the service last started, resolved ones included, newest first. Each one opens the records of all its turns. Records are chained by hash, so an edited or deleted line shows up as a break."
  : "Mock mode: each customer session run in this browser, plus one synthetic tamper test. Records are chained by hash, so an edited or deleted line shows up as a break.";

export function TraceList() {
  const rows = useResource("conversations", listConversations);
  const limited = rows.status === "error" && rows.error instanceof ApiError && rows.error.code === "rate_limited";
  return (
    <div className={styles.page}>
      <header className={`${styles.head} rise`}>
        <h1 className={styles.title}>Audit trail</h1>
        <p className={styles.lede}>{LEDE}</p>
      </header>
      {rows.status === "loading" ? (
        <div className={styles.list} aria-busy="true" aria-label="Loading conversations">
          {[0, 1, 2, 3, 4].map((i) => <span key={i} className="skeleton" style={{ height: 56 }} />)}
        </div>
      ) : rows.status === "error" ? (
        <Notice tone="error" icon={<WarningOctagon aria-hidden />} title="The audit trail could not be loaded"
          actions={<Button variant="secondary" size="sm" onClick={rows.reload}>Retry</Button>}>
          {limited ? "Too many reads from this address in the last minute. Retry in a minute." : "Nothing was changed. Retry in a moment."}
        </Notice>
      ) : rows.data.length === 0 ? (
        <Notice tone="empty" icon={<ListChecks aria-hidden />} title="No conversations yet">
          Run a customer conversation and its records appear here.
        </Notice>
      ) : (
        <ul className={styles.list}>
          {rows.data.map((row, i) => (
            <li key={row.id} className="rise-row" style={{ "--i": Math.min(i, 7) } as React.CSSProperties}>
              <Link href={`/audit/${row.latestTraceId}`} className={styles.row}>
                <span className={styles.who}>
                  <span className={`${styles.id} mono`}>{row.id}</span>
                  {row.isConversation ? (
                    <span className={`${styles.meta} mono`}>
                      {row.turns} {row.turns === 1 ? "turn" : "turns"}
                      {row.caseId ? ` · ${row.caseId}` : ""}
                    </span>
                  ) : null}
                </span>
                <span className={styles.label}><Outcome row={row} /></span>
                <ArrowRight aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

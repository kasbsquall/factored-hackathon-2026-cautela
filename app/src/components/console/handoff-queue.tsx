"use client";

import Link from "next/link";
import { ArrowClockwise, Tray, WarningOctagon } from "@phosphor-icons/react";
import type { Handoff } from "@/lib/api/types";
import type { Resource } from "@/lib/use-resource";
import { REASON } from "@/lib/labels";
import { utcStamp } from "@/lib/format";
import { StatusChip } from "@/components/ui/status-chip";
import { Notice } from "@/components/ui/notice";
import { Button } from "@/components/ui/button";
import styles from "./handoff-queue.module.css";

interface Props {
  resource: Resource<Handoff[]> & { reload: () => void };
  /** The case open at /console/[id]; marked as the current page. */
  selectedId: string | null;
  /** On /console, the case shown beside the queue on wide screens; highlighted there only. */
  previewId: string | null;
}

function QueueRow({ handoff: h, current, preview }: { handoff: Handoff; current: boolean; preview: boolean }) {
  const reason = REASON[h.transfer_reason.code];
  const confidence = h.transfer_reason.confidence;
  return (
    <Link href={`/console/${h.handoff_id}`} className={styles.row} aria-current={current ? "page" : undefined} data-preview={preview || undefined}>
      <span className={styles.top}>
        <StatusChip kind={reason.kind} size="sm">{reason.label}</StatusChip>
        <span className={`${styles.lang} mono`}>{h.language}</span>
      </span>
      <span className={styles.summary} lang={h.language === "pt" ? "pt" : "es"}>{h.request.summary}</span>
      <span className={`${styles.meta} mono`}>
        <span>{utcStamp(h.created_at)}</span>
        {typeof confidence === "number" ? <span>confidence {Math.round(confidence * 100)}&nbsp;%</span> : null}
      </span>
    </Link>
  );
}

export function HandoffQueue({ resource, selectedId, previewId }: Props) {
  const loading = resource.status === "loading";
  return (
    <section className={styles.queue} aria-labelledby="queue-title">
      <div className={styles.head}>
        <div className={styles.heading}>
          <h1 id="queue-title" className={styles.title}>Handoff queue</h1>
          {resource.status === "ready" ? <span className="mono">{resource.data.length} waiting</span> : <span className="mono" aria-hidden>&nbsp;</span>}
        </div>
        <Button variant="secondary" size="sm" className={styles.refresh} onClick={resource.reload} loading={loading} loadingLabel="Refreshing"
          icon={<ArrowClockwise aria-hidden />}>
          Refresh
        </Button>
      </div>
      {loading ? (
        <ul className={styles.list} aria-busy="true" aria-label="Loading handoffs">
          {[0, 1, 2, 3, 4].map((i) => (
            <li key={i} className={styles.skeletonRow}>
              <span className="skeleton" style={{ height: 22, width: 150 }} />
              <span className="skeleton" style={{ height: 12, width: "90%" }} />
              <span className="skeleton" style={{ height: 12, width: "60%" }} />
            </li>
          ))}
        </ul>
      ) : resource.status === "error" ? (
        <div className={styles.pad}>
          <Notice tone="error" icon={<WarningOctagon aria-hidden />} title="The queue could not be loaded"
            actions={<Button variant="secondary" size="sm" onClick={resource.reload}>Retry</Button>}>
            Nothing was changed. Retry in a moment.
          </Notice>
        </div>
      ) : resource.data.length === 0 ? (
        <div className={styles.pad}>
          <Notice tone="empty" icon={<Tray aria-hidden />} title="No handoffs waiting">
            Cases the assistant transfers appear here with their ready file.
          </Notice>
        </div>
      ) : (
        <ul className={styles.list}>
          {resource.data.map((h, i) => (
            <li key={h.handoff_id} className="rise-row" style={{ "--i": Math.min(i, 7), "--row-base": "0ms" } as React.CSSProperties}>
              <QueueRow handoff={h} current={h.handoff_id === selectedId} preview={h.handoff_id === previewId} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

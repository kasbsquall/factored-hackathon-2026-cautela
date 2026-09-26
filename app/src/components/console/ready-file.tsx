"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Copy, Scales, WarningCircle } from "@phosphor-icons/react";
import Link from "next/link";
import type { Handoff } from "@/lib/api/types";
import { LANGUAGE_NAME, REASON } from "@/lib/labels";
import { utcStamp } from "@/lib/format";
import { StatusChip } from "@/components/ui/status-chip";
import { RuleTag } from "@/components/ui/rule-tag";
import { Button, ButtonLink } from "@/components/ui/button";
import { ActionsSection, EvidenceSection, FactsSection, QuestionsSection, RequestSection } from "./ready-file-sections";
import styles from "./ready-file.module.css";

type CopyState = "idle" | "done" | "failed";

const RESET_MS = 1800;
const AMBIGUITY_RULE =
  "No policy rule. Ranker ambiguity rule: top match below 60 % or less than 15 points ahead of the second, or the customer rejected every candidate.";

const COPY_TEXT: Record<CopyState, { button: string; status: string }> = {
  idle: { button: "Copy handoff JSON", status: "" },
  done: { button: "Copied", status: "Handoff JSON copied to the clipboard." },
  failed: { button: "Copy failed", status: "The handoff JSON could not be copied. The browser blocked clipboard access." },
};

function useCopy(text: () => string): [CopyState, () => Promise<void>] {
  const [state, setState] = useState<CopyState>("idle");
  const timer = useRef<number | null>(null);

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text());
      setState("done");
    } catch {
      setState("failed");
    }
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), RESET_MS);
  }

  return [state, copy];
}

/** Date and time may wrap onto two lines, each part stays whole. */
function Stamp({ value }: { value: string }) {
  const [date, time] = utcStamp(value, true).split(", ");
  return <><span className={styles.nowrap}>{date},</span> <span className={styles.nowrap}>{time}</span></>;
}

function Confidence({ value }: { value: number | null | undefined }) {
  return (
    <div className={styles.confidence}>
      <p className="eyebrow">Transfer decision confidence</p>
      {typeof value === "number" ? (
        <>
          <p className={styles.big}>{Math.round(value * 100)}<span className={styles.unit}>%</span></p>
          <span className={styles.bar} aria-hidden><span style={{ "--v": value } as React.CSSProperties} /></span>
          <p className={styles.scale}><span>0 %</span><span>100 %</span></p>
        </>
      ) : (
        <p className={styles.none}>Not reported for this transfer</p>
      )}
    </div>
  );
}

export function ReadyFile({ handoff }: { handoff: Handoff }) {
  const reason = REASON[handoff.transfer_reason.code];
  const [copied, copyJson] = useCopy(() => JSON.stringify(handoff, null, 2));
  const copyIcon = copied === "done" ? <Check aria-hidden /> : copied === "failed" ? <WarningCircle aria-hidden /> : <Copy aria-hidden />;

  return (
    <article className={styles.file} aria-labelledby="file-title">
      <Link href="/console" className={styles.back}>
        <ArrowLeft aria-hidden />
        Queue
      </Link>
      <header className={`${styles.band} rise`}>
        <div className={styles.why}>
          <p className="eyebrow">Ready file · <span className="mono">{handoff.handoff_id}</span></p>
          <h1 id="file-title" className={styles.title}>{reason.label}</h1>
          <div className={styles.chips}>
            <StatusChip kind={reason.kind} size="sm">
              Reason code: <span className="mono">{handoff.transfer_reason.code}</span>
            </StatusChip>
            {handoff.transfer_reason.rule_ids.length ? (
              handoff.transfer_reason.rule_ids.map((id) => <RuleTag key={id} id={id} />)
            ) : (
              <span className={styles.norule}>
                <Scales aria-hidden />
                {handoff.transfer_reason.code === "low_confidence" ? AMBIGUITY_RULE : "No rule id attached"}
              </span>
            )}
          </div>
          <dl className={styles.meta}>
            <div><dt>Received</dt><dd className={`${styles.stamp} mono`}><Stamp value={handoff.created_at} /></dd></div>
            <div><dt>Language</dt><dd>{LANGUAGE_NAME[handoff.language] ?? handoff.language}</dd></div>
            <div><dt>Customer ref</dt><dd className="mono">{handoff.customer_ref}</dd></div>
            <div><dt>Trace</dt><dd className="mono">{handoff.trace_id}</dd></div>
          </dl>
        </div>
        <Confidence value={handoff.transfer_reason.confidence} />
      </header>

      <p className={`${styles.nots} rise`} style={{ "--i": 1 } as React.CSSProperties}>
        No transcript attached. Each verified fact names the tool it was read from.
      </p>

      <div className={styles.grid}>
        <div className={styles.main}>
          <QuestionsSection handoff={handoff} index={2} />
          <RequestSection handoff={handoff} index={3} />
          <FactsSection handoff={handoff} index={4} />
        </div>
        <div className={styles.side}>
          <ActionsSection handoff={handoff} index={3} />
          <EvidenceSection handoff={handoff} index={4} />
        </div>
      </div>

      <footer className={styles.footer}>
        <ButtonLink href={`/audit/${handoff.trace_id}`} variant="primary" size="sm" trailing={<ArrowRight aria-hidden />}>
          Open trace
        </ButtonLink>
        <Button variant="secondary" size="sm" onClick={copyJson} icon={copyIcon}>
          {COPY_TEXT[copied].button}
        </Button>
        <span className={`${styles.live} ${copied === "failed" ? styles.liveBad : ""}`} role="status">{COPY_TEXT[copied].status}</span>
      </footer>
    </article>
  );
}

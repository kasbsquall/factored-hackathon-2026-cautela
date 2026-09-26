"use client";

import type { ReactNode } from "react";
import { ArrowsClockwise, ChatText, Files, Question, SealCheck } from "@phosphor-icons/react";
import type { Handoff } from "@/lib/api/types";
import { ACTION_STATUS, LANGUAGE_NAME } from "@/lib/labels";
import { StatusChip } from "@/components/ui/status-chip";
import styles from "./ready-file.module.css";

function Section({ icon, title, count, children, className, index }: { icon: ReactNode; title: string; count?: number; children: ReactNode; className?: string; index: number }) {
  return (
    <section className={`${styles.section} ${className ?? ""} rise`} style={{ "--i": index } as React.CSSProperties}>
      <h2 className={styles.h}>
        {icon}
        {title}
        {count !== undefined ? <span className={`${styles.count} mono`}>{count}</span> : null}
      </h2>
      {children}
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className={styles.empty}>{children}</p>;
}

const DATE_TIME = /(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?)/;

/** Keeps "2026-09-18 22:14" on one line inside a fact. */
function FactText({ text }: { text: string }) {
  return (
    <>
      {text.split(DATE_TIME).map((part, i) => (i % 2 ? <span key={i} className={styles.nowrap}>{part}</span> : part))}
    </>
  );
}

/** Source ids like "auth.verify_otp:session:SYN-q7Lm2Xc9" may break after each colon; hyphenated ids stay whole. */
function SourceId({ id }: { id: string }) {
  const parts = id.split(":");
  return (
    <>
      {parts.map((part, i) => (
        <span key={i} className={part.includes("-") ? styles.nowrap : undefined}>{part}{i < parts.length - 1 ? <>:<wbr /></> : null}</span>
      ))}
    </>
  );
}

export function RequestSection({ handoff, index }: { handoff: Handoff; index: number }) {
  const { request, language } = handoff;
  return (
    <Section icon={<ChatText aria-hidden />} title="Request" index={index}>
      <blockquote className={styles.quote} lang={language === "pt" ? "pt" : "es"}>{request.summary}</blockquote>
      <p className={styles.caption}>
        Request summary in {LANGUAGE_NAME[language] ?? language}, after PII masking · intent{" "}
        <span className="mono">{request.intent}</span>
        {request.disputed_transaction_ids?.length ? (
          <> · disputed <span className="mono">{request.disputed_transaction_ids.join(", ")}</span></>
        ) : null}
      </p>
    </Section>
  );
}

export function QuestionsSection({ handoff, index }: { handoff: Handoff; index: number }) {
  return (
    <Section icon={<Question aria-hidden />} title="Open questions" count={handoff.open_questions.length} index={index} className={styles.questions}>
      {handoff.open_questions.length ? (
        <ol className={styles.qlist}>
          {handoff.open_questions.map((q) => <li key={q}>{q}</li>)}
        </ol>
      ) : (
        <Empty>No open questions recorded.</Empty>
      )}
    </Section>
  );
}

export function FactsSection({ handoff, index }: { handoff: Handoff; index: number }) {
  return (
    <Section icon={<SealCheck aria-hidden />} title="Verified facts" count={handoff.verified_facts.length} index={index}>
      {handoff.verified_facts.length ? (
        <table className={styles.table}>
          <thead>
            <tr><th scope="col">Fact</th><th scope="col">Read from</th></tr>
          </thead>
          <tbody>
            {handoff.verified_facts.map((f, i) => (
              <tr key={`${f.source}-${i}`} className="rise-row" style={{ "--i": Math.min(i, 7) } as React.CSSProperties}>
                <td><FactText text={f.fact} /></td>
                <td className="mono"><SourceId id={f.source} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No facts were verified before the transfer.</Empty>
      )}
    </Section>
  );
}

export function ActionsSection({ handoff, index }: { handoff: Handoff; index: number }) {
  return (
    <Section icon={<ArrowsClockwise aria-hidden />} title="Actions taken" count={handoff.actions_taken.length} index={index}>
      {handoff.actions_taken.length ? (
        <ul className={styles.actions}>
          {handoff.actions_taken.map((a, i) => {
            const status = ACTION_STATUS[a.status];
            return (
              <li key={`${a.action}-${i}`} className={`${styles.action} rise-row`} style={{ "--i": Math.min(i, 7) } as React.CSSProperties}>
                <span className="mono">{a.action}</span>
                <StatusChip kind={status.kind} size="sm">{status.label}</StatusChip>
                <span className={`${styles.record} mono`}>{a.record_id ?? "no record"}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <Empty>No actions were taken on the account.</Empty>
      )}
    </Section>
  );
}

export function EvidenceSection({ handoff, index }: { handoff: Handoff; index: number }) {
  const evidence = handoff.evidence ?? [];
  return (
    <Section icon={<Files aria-hidden />} title="Evidence" count={evidence.length} index={index}>
      {evidence.length ? (
        <ul className={styles.evidence}>
          {evidence.map((e) => <li key={e}>{e}</li>)}
        </ul>
      ) : (
        <Empty>No additional evidence attached.</Empty>
      )}
    </Section>
  );
}

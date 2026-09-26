"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import {
  ArrowUpRight, BookOpenText, CaretDown, ChatCircleText, CheckSquareOffset, Database, DotOutline, Funnel, HandTap,
  ListNumbers, LockKey, PencilSimpleLine, SealQuestion, TextAa, UserSwitch, WarningOctagon, type Icon,
} from "@phosphor-icons/react";
import { narrateTurn, causeText, type NarrationIcon, type NarrationStep, type RuleNote, type TurnCause } from "@/lib/narration";
import type { TurnRecord } from "./flow-types";
import styles from "./narration-panel.module.css";

const ICON: Record<NarrationIcon, Icon> = {
  gate: LockKey,
  read: TextAa,
  tool: Database,
  decide: Funnel,
  choose: ListNumbers,
  policy: BookOpenText,
  ask: SealQuestion,
  confirm: HandTap,
  write: PencilSimpleLine,
  verify: CheckSquareOffset,
  handoff: UserSwitch,
  security: WarningOctagon,
  reply: ChatCircleText,
  error: WarningOctagon,
  other: DotOutline,
};

function Rule({ rule }: { rule: RuleNote }) {
  return (
    <li className={styles.rule}>
      <span className={styles.ruleHead}>
        <span className="mono">{rule.id}</span>
        {rule.source ? <span className={`${styles.src} ${rule.source === "law" ? styles.law : ""}`}>{rule.source}</span> : null}
      </span>
      {rule.summary || rule.deadline ? (
        <span className={styles.ruleText}>
          {rule.summary}
          {rule.deadline ? <>{rule.summary ? ". " : ""}Computed deadline: <span className="num">{rule.deadline}</span></> : null}
        </span>
      ) : null}
    </li>
  );
}

function StepLine({ step, index }: { step: NarrationStep; index: number }) {
  const StepIcon = ICON[step.icon];
  return (
    <li className={`${styles.step} ${styles[step.tone]} rise-row`} style={{ "--i": Math.min(index, 7) } as React.CSSProperties}>
      <span className={styles.icon} aria-hidden><StepIcon /></span>
      <div className={styles.stepBody}>
        <p>{step.text}</p>
        {step.rules.length ? <ul className={styles.rules} aria-label="Rules">{step.rules.map((r) => <Rule key={r.id} rule={r} />)}</ul> : null}
      </div>
    </li>
  );
}

function TurnBlock({ record, number }: { record: TurnRecord; number: number }) {
  const n = narrateTurn(record.turn, record.cause);
  const headingId = useId();
  return (
    <li className={`${styles.turn} rise`} aria-labelledby={headingId}>
      <h3 id={headingId} className={styles.turnHead}>
        <span className={`${styles.turnNo} mono`}>Turn {number}</span>
        <span>{n.cause}</span>
      </h3>
      <p className={styles.stage}>Stage after this turn: <strong>{n.stage}</strong></p>
      <ol className={styles.steps}>
        {n.steps.map((step, i) => <StepLine key={i} step={step} index={i} />)}
      </ol>
      <p className={`${styles.foot} num`}>
        <span>Service time {n.serviceTime}</span>
        <span aria-hidden>·</span>
        <span>{n.llm}</span>
        <Link href={`/audit/${n.traceId}`} className={styles.trace}>Audit trace<ArrowUpRight aria-hidden /></Link>
      </p>
    </li>
  );
}

interface Props {
  turns: TurnRecord[];
  pending: TurnCause | null;
  failed: boolean;
  /** Before login there is no conversation at all. */
  active: boolean;
  className?: string;
}

/**
 * "What the system did (for reviewers)": each turn's decision trail in plain English, beside the chat on a wide
 * screen and collapsible under it on a narrow one. Always English, whatever the conversation language.
 */
export function NarrationPanel({ turns, pending, failed, active, className }: Props) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const count = turns.length;

  // Keep the newest turn in view inside the panel's own scroll area (wide screens); the page itself does not move.
  useEffect(() => {
    const box = listRef.current;
    if (box && box.scrollHeight > box.clientHeight) box.scrollTo({ top: box.scrollHeight, behavior: "auto" });
  }, [count, pending]);

  return (
    <section className={`${styles.panel} ${className ?? ""}`} aria-labelledby="narration-title" lang="en">
      <div className={styles.header}>
        <p className="eyebrow">For reviewers · English</p>
        <h2 id="narration-title" className={styles.title}>What the system did <span className={styles.sub}>(for reviewers)</span></h2>
        <p className={styles.lede}>
          Built from the decision trail each turn returns: steps, outcomes, rule ids and model usage. No language model writes
          this text.
        </p>
        <button type="button" className={styles.disclose} aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((v) => !v)}>
          <span>{open ? "Hide the explanation" : "Show the explanation"}</span>
          <span className={`${styles.count} num`}>{count === 1 ? "1 turn" : `${count} turns`}</span>
          <CaretDown aria-hidden className={styles.caret} />
        </button>
      </div>
      <div id={bodyId} className={styles.body} data-open={open} ref={listRef}>
        {count === 0 && !pending ? (
          <p className={styles.empty}>
            {active
              ? "Send the first message. Each turn's decision trail is explained here as it arrives."
              : "Nothing yet. Log in as a test customer and send a message; each turn's decision trail is explained here."}
          </p>
        ) : null}
        {count ? (
          <ol className={styles.turns}>
            {turns.map((record, i) => <TurnBlock key={record.id} record={record} number={i + 1} />)}
          </ol>
        ) : null}
        {pending ? (
          <div className={styles.pending} aria-busy="true">
            <p className={styles.turnHead}><span className={`${styles.turnNo} mono`}>Turn {count + 1}</span><span>{causeText(pending)}</span></p>
            <p className={styles.stage}>Waiting for the service to return its trail</p>
            <span className={`skeleton ${styles.skLine}`} />
            <span className={`skeleton ${styles.skLine} ${styles.skShort}`} />
            <span className={`skeleton ${styles.skLine}`} />
          </div>
        ) : failed ? (
          <p className={styles.failed}><WarningOctagon aria-hidden />The last request failed before the service returned a turn, so there is no trail to explain.</p>
        ) : null}
      </div>
    </section>
  );
}

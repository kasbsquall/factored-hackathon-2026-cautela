"use client";

import { useRef } from "react";
import Link from "next/link";
import { ArrowUpRight, CheckCircle, ClockCounterClockwise, FolderOpen, UserSwitch, type Icon } from "@phosphor-icons/react";
import type { ClaimWindow, TurnResponse } from "@/lib/api/types";
import { dateOnly } from "@/lib/format";
import type { CustomerCopy, UiLang } from "@/lib/i18n/customer";
import { spokenTextOf } from "@/lib/speech";
import { Button } from "@/components/ui/button";
import { Receipt, type ReceiptRow } from "@/components/ui/receipt";
import type { Entry } from "./flow-types";
import { ChargeLine } from "./charge-label";
import { RuleRef } from "./rule-ref";
import { ReadAloud } from "./read-aloud";
import { CaseNumber } from "./case-number";
import { receiptChecks, type Check } from "./receipt-checks";
import styles from "./outcome-receipt.module.css";

type ReceiptEntry = Extract<Entry, { kind: "receipt" }>;

interface Props {
  entry: ReceiptEntry;
  copy: CustomerCopy;
  lang: UiLang;
  demoClock: string | null;
  /** Every turn of the conversation, oldest first: the customer's earlier answers are in their trails. */
  turns: TurnResponse[];
  /** expires_at of the session granted after the one-time code, or null. */
  sessionUntil: string | null;
  /** Asks the service for a person; absent when a person already has the case. */
  onAskHuman?: () => void;
  busy?: boolean;
}

function Mono({ children }: { children: React.ReactNode }) {
  return <span className="mono">{children}</span>;
}

/** Retries the service reported for the failed write, from the turn's own trail. */
function writeAttempts(turn: ReceiptEntry["turn"]): number | null {
  const step = turn.trail.find((s) => s.step === "tool.open_dispute_case");
  return typeof step?.detail.attempts === "number" ? step.detail.attempts : null;
}

/**
 * The claim deadline the policy engine computed for this charge, with what its window rule says
 * (agent/policy/rules.yaml); without a computed date, the rule's window alone. Then every rule id with its source.
 */
function ruleRows(ruleIds: string[], claim: ClaimWindow | null, copy: CustomerCopy, lang: UiLang, demoClock: string | null): ReceiptRow[] {
  const windows = claim
    ? [{ label: copy.rowClaimDeadline, value: (
      <span className={styles.deadline}>
        <span className="num">{copy.claimUntil(dateOnly(claim.deadline, lang))}</span>
        {copy.ruleWindow[claim.rule_id] ? <small>{copy.ruleWindow[claim.rule_id]}</small> : null}
        {demoClock ? <small className="num">{copy.deadlineFrom(dateOnly(demoClock, lang))}</small> : null}
      </span>
    ) }]
    : ruleIds.filter((id) => copy.ruleWindow[id]).map((id) => ({ label: copy.rowClaimDeadline, value: copy.ruleWindow[id] ?? "" }));
  return [...windows, ...ruleIds.map((id) => ({ label: copy.rowRule, value: <RuleRef id={id} copy={copy} /> }))];
}

function Checks({ checks, copy }: { checks: Check[]; copy: CustomerCopy }) {
  if (!checks.length) return null;
  return (
    <section className={styles.section} aria-label={copy.checksTitle}>
      <h4 className="eyebrow">{copy.checksTitle}</h4>
      <ul className={styles.list}>
        {checks.map((c, i) => (
          <li key={c.id} className={`${styles.item} rise-row`} style={{ "--i": Math.min(i, 7) } as React.CSSProperties} data-check={c.id}>
            <CheckCircle className={styles.tick} aria-hidden />
            <span className={styles.body}>
              <span>{c.text}</span>
              {c.value ? (
                <span className={styles.value} data-speech={c.spoken}>
                  {c.valueLabel ? `${c.valueLabel}: ` : null}
                  <span className="num">
                    {/* Each fact stays on one line ("COP 48.900" never splits); the list wraps between facts. */}
                    {c.value.split(" · ").map((part, j) => (
                      <span key={j}>{j ? " · " : null}<span className={styles.nowrap}>{part}</span></span>
                    ))}
                  </span>
                </span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

interface NextItem {
  key: string;
  icon: Icon;
  label?: string;
  text: string;
}

function Next({ items, copy }: { items: NextItem[]; copy: CustomerCopy }) {
  if (!items.length) return null;
  return (
    <section className={styles.section} aria-label={copy.nextTitle}>
      <h4 className="eyebrow">{copy.nextTitle}</h4>
      <ul className={styles.list}>
        {items.map(({ key, icon: ItemIcon, label, text }) => (
          <li key={key} className={styles.item}>
            <ItemIcon className={styles.nextIcon} aria-hidden />
            <span className={styles.body}>
              {label ? <span className={styles.label}>{label}</span> : null}
              <span className={label ? styles.small : undefined}>{text}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Built only from what the turn returned, what GET /cases/{id} read back and the session the one-time code opened. */
export function OutcomeReceipt({ entry, copy, lang, demoClock, turns, sessionUntil, onAskHuman, busy = false }: Props) {
  const { turn, caseView } = entry;
  const stackRef = useRef<HTMLDivElement>(null);
  const reason = turn.transfer_reason;
  const listen = (
    <ReadAloud id={`receipt-${entry.id}`} lang={lang} copy={copy} label={copy.listenReceipt}
      text={() => (stackRef.current ? spokenTextOf(stackRef.current) : "")} />
  );
  const charge: ReceiptRow[] = entry.charge || entry.chargeView
    ? [{ label: copy.rowCharge, value: <ChargeLine label={entry.charge} charge={entry.chargeView} lang={lang} /> }] : [];
  const handoff: ReceiptRow[] = turn.handoff_id ? [{ label: copy.rowHandoff, value: <Mono>{turn.handoff_id}</Mono> }] : [];
  const foot = (
    <>
      <span>{copy.trace} {turn.trace_id}</span>
      <Link href={`/audit/${turn.trace_id}`} className={styles.trace}>{copy.viewTrace}<ArrowUpRight aria-hidden /></Link>
    </>
  );

  // A person asked for after the case was filed: the handoff carries the case, so the receipt shows what they receive.
  if (turn.case && turn.stage === "handed_off" && reason === "customer_requested_human") {
    const checks = receiptChecks({ turn, caseView, chargeView: entry.chargeView, turns, sessionUntil, copy, lang });
    const next: NextItem[] = [
      { key: "person", icon: UserSwitch, text: copy.nextTakeover },
      ...(caseView?.status === "open" ? [{ key: "status", icon: FolderOpen, text: copy.nextOpen }] : []),
    ];
    return (
      <div className={styles.stack} ref={stackRef}>
        <Receipt kind="hu" title={copy.receiptTakeover}
          rows={[{ label: copy.rowReason, value: copy.transferReason[reason] ?? reason }, ...handoff]} foot={foot}>
          <CaseNumber id={turn.case.case_id} copy={copy}>{listen}</CaseNumber>
          <p className={styles.lede}>{copy.takeoverBody}</p>
          <Checks checks={checks} copy={copy} />
          <Next items={next} copy={copy} />
        </Receipt>
      </div>
    );
  }

  if (turn.case) {
    const readBack = caseView !== null && caseView.case_id === turn.case.case_id;
    const verified = turn.case.verified && readBack;
    const review = caseView?.status === "pending_human_review";
    const checks = receiptChecks({ turn, caseView, chargeView: entry.chargeView, turns, sessionUntil, copy, lang });
    const next: NextItem[] = [
      ...(caseView ? [review ? { key: "status", icon: UserSwitch, text: copy.confirmReviewLive } : { key: "status", icon: FolderOpen, text: copy.nextOpen }] : []),
      { key: "typical", icon: ClockCounterClockwise, label: copy.typicalLabel, text: copy.typicalResolution },
    ];
    return (
      <div className={styles.stack} ref={stackRef}>
        <Receipt
          kind={verified ? "ok" : "stop"}
          title={!verified ? copy.receiptNotVerified : review ? copy.receiptReview : copy.receiptOpen}
          rows={[...ruleRows(caseView?.policy_rule_ids ?? [], turn.case.claim_window, copy, lang, demoClock), ...handoff]}
          foot={foot}
        >
          <CaseNumber id={turn.case.case_id} copy={copy}>{listen}</CaseNumber>
          {!verified ? <p className={styles.note}>{copy.notVerifiedBody}</p> : null}
          <Checks checks={checks} copy={copy} />
          <Next items={next} copy={copy} />
        </Receipt>
        {review && reason ? (
          <Receipt kind="stop" index={1} title={copy.receiptStop}
            rows={[{ label: copy.rowReason, value: copy.transferReason[reason] ?? reason }, { label: copy.rowNext, value: copy.nextPerson }]} />
        ) : null}
        {turn.stage === "resolved" && onAskHuman ? (
          <section className={`${styles.ask} rise`} style={{ "--i": 1 } as React.CSSProperties} aria-label={copy.askPersonTitle}>
            <div className={styles.askText}>
              <h4 className={styles.askTitle}>{copy.askPersonTitle}</h4>
              <p className={styles.note}>{copy.askPersonBody}</p>
            </div>
            <Button variant="secondary" size="sm" icon={<UserSwitch aria-hidden />} onClick={onAskHuman} disabled={busy}>{copy.askTakeover}</Button>
          </section>
        ) : null}
      </div>
    );
  }

  if (turn.stage === "recognized") {
    return (
      <div className={styles.stack} ref={stackRef}>
        <Receipt kind="ok" title={copy.receiptRecognized}
          rows={[...charge, { label: copy.rowChanges, value: copy.noChanges }]} foot={foot}>
          <p className={styles.note}>{copy.recognizedBody}</p>
        </Receipt>
        <div className={styles.tools}>{listen}</div>
      </div>
    );
  }

  const failed = reason === "tool_failure";
  const attempts = failed ? writeAttempts(turn) : null;
  return (
    <div className={styles.stack} ref={stackRef}>
      <Receipt
        kind={failed ? "bad" : "hu"}
        title={failed ? copy.receiptFail : copy.receiptHuman}
        rows={[
          ...charge,
          ...(reason ? [{ label: copy.rowReason, value: copy.transferReason[reason] ?? reason }] : []),
          ...(attempts !== null ? [{ label: copy.rowAttempts, value: <Mono>{attempts}</Mono> }] : []),
          ...handoff,
        ]}
        foot={foot}
      >
        <p className={styles.note}>{attempts !== null ? copy.failBody(attempts) : copy.humanBody}</p>
      </Receipt>
      <div className={styles.tools}>{listen}</div>
    </div>
  );
}

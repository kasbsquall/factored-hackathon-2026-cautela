"use client";

import Link from "next/link";
import { ArrowUpRight } from "@phosphor-icons/react";
import type { Language } from "@/lib/api/types";
import type { CustomerCopy } from "@/lib/i18n/customer";
import { Receipt, type ReceiptRow } from "@/components/ui/receipt";
import type { Entry } from "./flow-types";
import { ChargeText } from "./charge-label";
import { RuleRef } from "./rule-ref";
import styles from "./outcome-receipt.module.css";

type ReceiptEntry = Extract<Entry, { kind: "receipt" }>;

function Mono({ children }: { children: React.ReactNode }) {
  return <span className="mono">{children}</span>;
}

/** Retries the service reported for the failed write, from the turn's own trail. */
function writeAttempts(turn: ReceiptEntry["turn"]): number | null {
  const step = turn.trail.find((s) => s.step === "tool.open_dispute_case");
  return typeof step?.detail.attempts === "number" ? step.detail.attempts : null;
}

/** Claim window of each window rule (agent/policy/rules.yaml), then every rule id with its source. */
function ruleRows(ruleIds: string[], copy: CustomerCopy): ReceiptRow[] {
  const windows = ruleIds.filter((id) => copy.ruleWindow[id]).map((id) => ({ label: copy.rowClaimDeadline, value: copy.ruleWindow[id] ?? "" }));
  return [...windows, ...ruleIds.map((id) => ({ label: copy.rowRule, value: <RuleRef id={id} copy={copy} /> }))];
}

/** Built only from what the turn returned and what GET /cases/{id} read back. */
export function OutcomeReceipt({ entry, copy, lang }: { entry: ReceiptEntry; copy: CustomerCopy; lang: Language }) {
  const { turn, caseView } = entry;
  const reason = turn.transfer_reason;
  const charge: ReceiptRow[] = entry.charge ? [{ label: copy.rowCharge, value: <ChargeText label={entry.charge} lang={lang} /> }] : [];
  const handoff: ReceiptRow[] = turn.handoff_id ? [{ label: copy.rowHandoff, value: <Mono>{turn.handoff_id}</Mono> }] : [];
  const foot = (
    <>
      <span>{copy.trace} {turn.trace_id}</span>
      <Link href={`/audit/${turn.trace_id}`} className={styles.trace}>{copy.viewTrace}<ArrowUpRight aria-hidden /></Link>
    </>
  );

  if (turn.case) {
    const readBack = caseView !== null && caseView.case_id === turn.case.case_id;
    const verified = turn.case.verified && readBack;
    const review = caseView?.status === "pending_human_review";
    return (
      <div className={styles.stack}>
        <Receipt
          kind={verified ? "ok" : "stop"}
          title={!verified ? copy.receiptNotVerified : review ? copy.receiptReview : copy.receiptOpen}
          rows={[
            { label: copy.rowCase, value: <Mono>{turn.case.case_id}</Mono> },
            ...(caseView ? [{ label: copy.rowStatus, value: review ? copy.statusReview : copy.statusOpen }] : []),
            ...charge,
            ...ruleRows(caseView?.policy_rule_ids ?? [], copy),
            ...handoff,
          ]}
          foot={foot}
        >
          <p className={styles.note}>{verified ? copy.readBackOk : copy.notVerifiedBody}</p>
        </Receipt>
        {review && reason ? (
          <Receipt kind="stop" index={1} title={copy.receiptStop}
            rows={[{ label: copy.rowReason, value: copy.transferReason[reason] ?? reason }, { label: copy.rowNext, value: copy.nextPerson }]} />
        ) : null}
      </div>
    );
  }

  const failed = reason === "tool_failure";
  const attempts = failed ? writeAttempts(turn) : null;
  return (
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
  );
}

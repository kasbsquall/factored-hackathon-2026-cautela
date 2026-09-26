"use client";

import Link from "next/link";
import { ArrowUpRight } from "@phosphor-icons/react";
import type { ClaimWindow } from "@/lib/api/types";
import { dateOnly } from "@/lib/format";
import type { CustomerCopy, UiLang } from "@/lib/i18n/customer";
import { Receipt, type ReceiptRow } from "@/components/ui/receipt";
import type { Entry } from "./flow-types";
import { ChargeLine } from "./charge-label";
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

/**
 * The claim deadline the policy engine computed for this charge, with what its window rule says
 * (agent/policy/rules.yaml); without a computed date, the rule's window alone. Then every rule id with its source.
 */
function ruleRows(ruleIds: string[], claim: ClaimWindow | null, copy: CustomerCopy, lang: UiLang): ReceiptRow[] {
  const windows = claim
    ? [{ label: copy.rowClaimDeadline, value: (
      <span className={styles.deadline}>
        <span className="num">{copy.claimUntil(dateOnly(claim.deadline, lang))}</span>
        {copy.ruleWindow[claim.rule_id] ? <small>{copy.ruleWindow[claim.rule_id]}</small> : null}
      </span>
    ) }]
    : ruleIds.filter((id) => copy.ruleWindow[id]).map((id) => ({ label: copy.rowClaimDeadline, value: copy.ruleWindow[id] ?? "" }));
  return [...windows, ...ruleIds.map((id) => ({ label: copy.rowRule, value: <RuleRef id={id} copy={copy} /> }))];
}

/** Built only from what the turn returned and what GET /cases/{id} read back. */
export function OutcomeReceipt({ entry, copy, lang }: { entry: ReceiptEntry; copy: CustomerCopy; lang: UiLang }) {
  const { turn, caseView } = entry;
  const reason = turn.transfer_reason;
  const charge: ReceiptRow[] = entry.charge || entry.chargeView
    ? [{ label: copy.rowCharge, value: <ChargeLine label={entry.charge} charge={entry.chargeView} lang={lang} /> }] : [];
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
            ...ruleRows(caseView?.policy_rule_ids ?? [], turn.case.claim_window, copy, lang),
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

  if (turn.stage === "recognized") {
    return (
      <Receipt kind="ok" title={copy.receiptRecognized}
        rows={[...charge, { label: copy.rowChanges, value: copy.noChanges }]} foot={foot}>
        <p className={styles.note}>{copy.recognizedBody}</p>
      </Receipt>
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

"use client";

import { ArrowRight, HandTap, UserSwitch } from "@phosphor-icons/react";
import type { CustomerCopy, UiLang } from "@/lib/i18n/customer";
import { dateOnly, timeOnly } from "@/lib/format";
import { Button } from "@/components/ui/button";
import type { Entry } from "./flow-types";
import { ChargeBlock, ChargeLabel } from "./charge-label";
import { WithRuleId } from "./rule-ref";
import styles from "./confirm-card.module.css";

interface Props {
  entry: Extract<Entry, { kind: "confirm" }>;
  copy: CustomerCopy;
  lang: UiLang;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Explicit confirmation before a write. The service holds the token; this card only answers yes or no to its id. */
export function ConfirmCard({ entry, copy, lang, onConfirm, onCancel }: Props) {
  const { confirmation: c, state } = entry;
  const block = c.tool === "block_card";
  const locked = state !== "pending";
  return (
    <section className={styles.card} aria-labelledby={`${entry.id}-title`}>
      <h2 id={`${entry.id}-title`} className={styles.title}>
        <HandTap aria-hidden />
        {block ? copy.confirmBlockTitle : copy.confirmTitle}
      </h2>
      {c.charge ? <ChargeBlock charge={c.charge} copy={copy} lang={lang} /> : <ChargeLabel label={c.label} lang={lang} />}
      <p className={styles.body}>{block ? copy.confirmBlockBody : copy.confirmBody}</p>
      {c.review ? (
        <ul className={styles.points}>
          <li><UserSwitch aria-hidden /><span>{copy.confirmReviewLive}</span></li>
        </ul>
      ) : null}
      <p className={`${styles.valid} num`}>
        {copy.confirmValid(timeOnly(c.expires_at, lang))}
        {c.claim_window ? <> <WithRuleId text={copy.claimNote(dateOnly(c.claim_window.deadline, lang), c.claim_window.rule_id)} id={c.claim_window.rule_id} /></> : null}
      </p>
      <div className={styles.actions}>
        <Button loading={state === "working"} loadingLabel={copy.opening} aria-disabled={locked || undefined}
          onClick={locked ? undefined : onConfirm} trailing={<ArrowRight aria-hidden />}
          className={state === "cancelled" || state === "done" ? styles.muted : undefined}>
          {block ? copy.confirmBlock : copy.confirm}
        </Button>
        <Button variant="secondary" aria-disabled={locked || undefined} onClick={locked ? undefined : onCancel}
          className={state !== "pending" && state !== "cancelled" ? styles.muted : undefined}>
          {copy.cancel}
        </Button>
      </div>
    </section>
  );
}

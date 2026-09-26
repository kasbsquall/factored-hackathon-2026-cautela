"use client";

import { ArrowRight, CalendarBlank, CreditCard, MapPin, Receipt, SealQuestion, Tag } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import type { CustomerCopy, UiLang } from "@/lib/i18n/customer";
import { dateOnly } from "@/lib/format";
import { Button } from "@/components/ui/button";
import type { Entry } from "./flow-types";
import { ReasonChips, cardText, channelIcon, chargeAmount, chargeDay } from "./charge-facts";
import { WithRuleId } from "./rule-ref";
import styles from "./recognize-card.module.css";

interface Props {
  entry: Extract<Entry, { kind: "recognize" }>;
  copy: CustomerCopy;
  lang: UiLang;
  onAnswer: (recognized: boolean) => void;
}

function Fact({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className={styles.fact}>
      <dt>{icon}<span>{label}</span></dt>
      <dd>{children}</dd>
    </div>
  );
}

/**
 * "Do you recognize it?": the charge exactly as the tools read it, before any dispute. Recognizing it ends the
 * conversation with nothing written; not recognizing it asks for the dispute confirmation next.
 */
export function RecognizeCard({ entry, copy, lang, onAnswer }: Props) {
  const { recognition: r, state } = entry;
  const c = r.charge;
  const locked = state !== "pending";
  const ChannelIcon = channelIcon(c.channel);
  const card = cardText(c, copy);
  const category = [c.category ? copy.category[c.category] ?? c.category : null, c.merchant_category ? copy.mcc(c.merchant_category) : null]
    .filter(Boolean).join(" · ");
  const place = [c.city, c.country].filter(Boolean).join(", ");
  const status = [c.transaction_type ? copy.txType[c.transaction_type] ?? c.transaction_type : null,
    c.transaction_status ? copy.txStatus[c.transaction_status] ?? c.transaction_status : null].filter(Boolean).join(" · ");
  const chosen = state === "yes" || state === "working-yes" ? "yes" : state === "no" || state === "working-no" ? "no" : null;

  return (
    <section className={styles.card} aria-labelledby={`${entry.id}-title`}>
      <h2 id={`${entry.id}-title`} className={styles.title}>
        <SealQuestion aria-hidden />
        {copy.recognizeTitle}
      </h2>
      <p className={styles.lede}>{copy.recognizeLede}</p>

      <div className={styles.evidence}>
        <div className={styles.head}>
          <p className={`${styles.merchant} mono`}>{c.merchant_name ?? r.label}</p>
          <p className={`${styles.amount} mono`}>{chargeAmount(c, lang)}</p>
        </div>
        <dl className={styles.facts}>
          {category ? <Fact icon={<Tag aria-hidden />} label={copy.factCategory}>{category}</Fact> : null}
          <Fact icon={<CalendarBlank aria-hidden />} label={copy.factWhen}><span className="num">{chargeDay(c, lang)}</span></Fact>
          {c.channel ? <Fact icon={<ChannelIcon aria-hidden />} label={copy.factChannel}>{copy.channel[c.channel] ?? c.channel}</Fact> : null}
          {place ? <Fact icon={<MapPin aria-hidden />} label={copy.factPlace}>{place}</Fact> : null}
          {card ? <Fact icon={<CreditCard aria-hidden />} label={copy.factCard}><span className="num">{card}</span></Fact> : null}
          {status ? <Fact icon={<Receipt aria-hidden />} label={copy.factStatus}>{status}</Fact> : null}
        </dl>
      </div>

      <div className={styles.why}>
        <p className="eyebrow">{copy.whyMatched}</p>
        <ReasonChips reasons={r.reasons} copy={copy} lang={lang} />
      </div>

      <div className={styles.actions}>
        <Button variant="secondary" loading={state === "working-yes"} loadingLabel={copy.recognizeWorking} aria-pressed={chosen === "yes"}
          aria-disabled={locked || undefined} onClick={locked ? undefined : () => onAnswer(true)}
          className={locked && chosen !== "yes" ? styles.muted : undefined}>
          {copy.recognizeYes}
        </Button>
        <Button loading={state === "working-no"} loadingLabel={copy.recognizeWorking} aria-pressed={chosen === "no"}
          aria-disabled={locked || undefined} onClick={locked ? undefined : () => onAnswer(false)} trailing={<ArrowRight aria-hidden />}
          className={locked && chosen !== "no" ? styles.muted : undefined}>
          {copy.recognizeNo}
        </Button>
      </div>
      {r.claim_window ? (
        <p className={`${styles.claim} num`}><WithRuleId text={copy.claimNote(dateOnly(r.claim_window.deadline, lang), r.claim_window.rule_id)} id={r.claim_window.rule_id} /></p>
      ) : null}
    </section>
  );
}

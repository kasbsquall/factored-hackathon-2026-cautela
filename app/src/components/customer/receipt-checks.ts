import type { CaseView, ChargeView, TurnResponse } from "@/lib/api/types";
import type { CustomerCopy, UiLang } from "@/lib/i18n/customer";
import { dateOnly, timeOnly } from "@/lib/format";
import { spellId } from "@/lib/speech";
import { cardText, chargeAmount } from "./charge-facts";

export type CheckId = "identity" | "charge" | "confirmed" | "case" | "readBack";

/** One line of "What we checked": what was verified and the value it was verified with. */
export interface Check {
  id: CheckId;
  text: string;
  value: string;
  /** Prefix shown before the value ("Status in the system"). */
  valueLabel?: string;
  /** How the value is read aloud, when the written form reads badly (a spelled id, a masked card). */
  spoken?: string;
  mono?: boolean;
}

export interface CheckInput {
  turn: TurnResponse;
  /** GET /cases/{id} read back after the write, or null. */
  caseView: CaseView | null;
  /** The charge as the tools read it (the confirmation's ChargeView), or null. */
  chargeView: ChargeView | null;
  /** Every turn of this conversation, oldest first; the customer's answers are in their trails. */
  turns: TurnResponse[];
  /** expires_at of the session POST /auth/verify granted after the one-time code, or null without one. */
  sessionUntil: string | null;
  copy: CustomerCopy;
  lang: UiLang;
}

function hasStep(turns: TurnResponse[], step: string, outcome: string): boolean {
  return turns.some((t) => t.trail.some((s) => s.step === step && s.outcome === outcome));
}

function chargeCheck(charge: ChargeView, copy: CustomerCopy, lang: UiLang): Check | null {
  const amount = charge.amount !== null && charge.currency ? chargeAmount(charge, lang) : null;
  const day = charge.transaction_date ? dateOnly(charge.transaction_date, lang) : null;
  const card = cardText(charge, copy);
  const written = [charge.merchant_name, day, amount, card].filter(Boolean);
  if (!charge.merchant_name && !amount) return null;
  const type = charge.card_type ? copy.cardType[charge.card_type] ?? charge.card_type : null;
  const spoken = [charge.merchant_name, day, amount, charge.card_last4 ? copy.cardSpoken(type, charge.card_last4) : null].filter(Boolean);
  return { id: "charge", text: copy.checkCharge, value: written.join(" · "), spoken: spoken.join(", ") };
}

/**
 * The checks the receipt can show, each only when the response carries the data behind it. Nothing is inferred:
 * a missing read-back, charge or confirmation step simply drops its line.
 */
export function receiptChecks({ turn, caseView, chargeView, turns, sessionUntil, copy, lang }: CheckInput): Check[] {
  const checks: Check[] = [];
  const all = turns.includes(turn) ? turns : [...turns, turn];

  if (sessionUntil) {
    checks.push({ id: "identity", text: copy.checkIdentity, value: copy.checkIdentityValue(timeOnly(sessionUntil, lang)) });
  }
  const charge = chargeView ? chargeCheck(chargeView, copy, lang) : null;
  if (charge) checks.push(charge);
  if (hasStep([turn], "confirm.answer", "accepted")) {
    const denied = hasStep(all, "recognize.answer", "not_recognized");
    checks.push({
      id: "confirmed",
      text: denied ? copy.checkConfirmed : copy.checkConfirmedOnly,
      value: (denied ? [copy.recognizeNo, copy.confirm] : [copy.confirm]).map((s) => `“${s}”`).join(" · "),
    });
  }
  if (turn.case) {
    checks.push({ id: "case", text: copy.checkCase, value: turn.case.case_id, spoken: spellId(turn.case.case_id), mono: true });
    if (turn.case.verified && caseView && caseView.case_id === turn.case.case_id) {
      checks.push({
        id: "readBack",
        text: copy.checkReadBack,
        valueLabel: copy.rowStatus,
        value: caseView.status === "pending_human_review" ? copy.statusReview : copy.statusOpen,
      });
    }
  }
  return checks;
}

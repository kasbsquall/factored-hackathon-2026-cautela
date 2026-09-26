import type { MatchCode, MatchReason } from "@/lib/api/types";

/**
 * English wording of the match reasons, for the reviewer view. Source: agent/orchestrator/evidence.py LABELS; `value`
 * is the number the service put in its own label (percent or days), so nothing here is computed on the client.
 */
const EN: Record<MatchCode, (n: number | null) => string> = {
  amount_exact: () => "Same amount you gave",
  amount_close: (n) => `Amount within ${n}% of the one you gave`,
  date_same_day: () => "Same day as the date you gave",
  date_within_days: (n) => `${n} ${n === 1 ? "day" : "days"} from the date you gave`,
  date_in_range: () => "Inside the period you mentioned",
  date_near: (n) => `${n} ${n === 1 ? "day" : "days"} from the period you mentioned`,
  merchant_named: () => "Merchant you mentioned",
  type_match: () => "Type of operation you mentioned",
  channel_match: () => "Channel you mentioned",
  city_match: () => "City you mentioned",
  only_fit: (n) => `Only charge in the last ${n} days that fits everything you said`,
  customer_selected: () => "You picked it from the list",
  customer_reference: () => "Reference you typed",
};

/** Reason codes that need their number to read correctly; without it, the service's own label is kept. */
const NEEDS_VALUE = new Set<MatchCode>(["amount_close", "date_within_days", "date_near", "only_fit"]);

export function reasonLabelEn(reason: MatchReason): string {
  const make = EN[reason.code];
  if (!make || (NEEDS_VALUE.has(reason.code) && reason.value === null)) return reason.label;
  return make(reason.value);
}

/** Third-person phrase for the narration, e.g. "same day", "amount within 3%". */
const NARRATION: Record<MatchCode, (n: number | null) => string> = {
  amount_exact: () => "same amount",
  amount_close: (n) => (n === null ? "close amount" : `amount within ${n}%`),
  date_same_day: () => "same day",
  date_within_days: (n) => (n === null ? "close date" : `${n} ${n === 1 ? "day" : "days"} from the stated date`),
  date_in_range: () => "inside the stated period",
  date_near: (n) => (n === null ? "near the stated period" : `${n} ${n === 1 ? "day" : "days"} from the stated period`),
  merchant_named: () => "merchant named",
  type_match: () => "type of operation named",
  channel_match: () => "channel named",
  city_match: () => "city named",
  only_fit: (n) => (n === null ? "the only charge that fits every cue" : `the only charge of the last ${n} days that fits every cue`),
  customer_selected: () => "picked by the customer",
  customer_reference: () => "reference typed by the customer",
};

export function reasonPhrase(code: string, value: number | null = null): string {
  const make = NARRATION[code as MatchCode];
  return make ? make(value) : code;
}

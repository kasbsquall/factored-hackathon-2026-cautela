/**
 * Charge evidence and match reasons for mock mode, following agent/orchestrator/evidence.py: the charge fields are
 * the fixture's tool data, and a reason appears only when its cue test passes for that charge (amount within 25 %,
 * date within 2 days of the day or period given, merchant named, the only charge that fits every cue). Labels are copied
 * from evidence.py LABELS; keep them in sync by hand.
 */
import type { ChargeView, Language, MatchCode, MatchReason, TransactionView } from "../types";
import type { MockCustomer } from "./fixtures/customers";

const LABELS: Record<Language, Record<MatchCode, string>> = {
  es: {
    amount_exact: "Mismo monto que indicaste",
    amount_close: "Monto a {n}% del que indicaste",
    date_same_day: "El mismo día que indicaste",
    date_within_days: "A {n} {days} de la fecha que indicaste",
    date_in_range: "Dentro del período que mencionaste",
    date_near: "A {n} {days} del período que mencionaste",
    merchant_named: "Comercio que mencionaste",
    type_match: "Tipo de operación que mencionaste",
    channel_match: "Canal que mencionaste",
    city_match: "Ciudad que mencionaste",
    only_fit: "Único cargo de los últimos {n} días que coincide con todo lo que dijiste",
    customer_selected: "Lo elegiste de la lista",
    customer_reference: "Referencia que escribiste",
  },
  pt: {
    amount_exact: "Mesmo valor que você informou",
    amount_close: "Valor a {n}% do que você informou",
    date_same_day: "No mesmo dia que você informou",
    date_within_days: "A {n} {days} da data que você informou",
    date_in_range: "Dentro do período que você mencionou",
    date_near: "A {n} {days} do período que você mencionou",
    merchant_named: "Loja que você mencionou",
    type_match: "Tipo de operação que você mencionou",
    channel_match: "Canal que você mencionou",
    city_match: "Cidade que você mencionou",
    only_fit: "Única cobrança dos últimos {n} dias que bate com tudo o que você disse",
    customer_selected: "Você escolheu na lista",
    customer_reference: "Referência que você escreveu",
  },
};
const DAYS: Record<Language, [string, string]> = { es: ["día", "días"], pt: ["dia", "dias"] };
const WINDOW_DAYS = 90;
const AMOUNT_TOLERANCE = 0.25;
const DATE_SLACK_DAYS = 2;
/** A hint this tight is a day the customer named; wider hints are periods, as in evidence.py. */
const DAY_TOLERANCE = 1;

export function reason(code: MatchCode, lang: Language, value: number | null = null): MatchReason {
  const [one, many] = DAYS[lang];
  const label = LABELS[lang][code].replace("{n}", String(value ?? "")).replace("{days}", value === 1 ? one : many);
  return { code, label, value };
}

function dayOf(t: TransactionView): number {
  return Date.parse((t.transaction_date ?? "").slice(0, 10));
}

/** The cue tests that pass for one charge, in the order evidence.py reports them. */
function cueReasons(customer: MockCustomer, t: TransactionView, lang: Language): { reasons: MatchReason[]; fitsAll: boolean } {
  const { amount, currency, date_hint: hint, date_tolerance_days: tol = 3, merchant_hint: merchant } = customer.hints;
  const reasons: MatchReason[] = [];
  let fitsAll = true;
  if (typeof amount === "number") {
    const sameCurrency = !currency || t.currency === currency;
    const pct = t.amount === null ? Infinity : (Math.abs(t.amount - amount) / amount) * 100;
    if (sameCurrency && pct <= AMOUNT_TOLERANCE * 100) reasons.push(pct < 0.5 ? reason("amount_exact", lang) : reason("amount_close", lang, Math.max(1, Math.round(pct))));
    else fitsAll = false;
  }
  if (hint) {
    const days = Math.round(Math.abs(dayOf(t) - Date.parse(hint)) / 86_400_000);
    const dist = Math.max(0, days - tol); // distance to the period [hint - tol, hint + tol]
    if (dist > DATE_SLACK_DAYS) fitsAll = false;
    else if (tol <= DAY_TOLERANCE) reasons.push(days === 0 ? reason("date_same_day", lang) : reason("date_within_days", lang, days));
    else reasons.push(dist === 0 ? reason("date_in_range", lang) : reason("date_near", lang, dist));
  }
  if (merchant) {
    if ((t.merchant_name ?? "").toLowerCase().includes(merchant.toLowerCase())) reasons.push(reason("merchant_named", lang));
    else fitsAll = false;
  }
  return { reasons, fitsAll };
}

export function matchReasons(customer: MockCustomer, transactionId: string, lang: Language): MatchReason[] {
  const t = customer.transactions.find((x) => x.transaction_id === transactionId);
  if (!t) return [];
  const { reasons } = cueReasons(customer, t, lang);
  const fitting = customer.transactions.filter((x) => cueReasons(customer, x, lang).fitsAll);
  if (reasons.length && fitting.length === 1 && fitting[0]?.transaction_id === transactionId) {
    reasons.push(reason("only_fit", lang, WINDOW_DAYS));
  }
  return reasons;
}

/** Same fields as evidence.py charge_details; the card comes from the fixture's masked product list. */
export function chargeOf(customer: MockCustomer, t: TransactionView): ChargeView {
  const card = t.product_id ? customer.cards[t.product_id] : undefined;
  return {
    transaction_date: t.transaction_date, amount: t.amount, currency: t.currency, merchant_name: t.merchant_name,
    merchant_category: t.merchant_category ?? null, category: t.transaction_category ?? null, channel: t.channel,
    city: t.transaction_city, country: t.transaction_country, card_type: card?.card_type ?? null,
    card_last4: card?.card_last4 ?? null, transaction_type: t.transaction_type, transaction_status: t.transaction_status,
  };
}

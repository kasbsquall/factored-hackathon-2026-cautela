import type { Language } from "@/lib/api/types";

const ZERO_DECIMALS = new Set(["COP", "CLP", "PYG"]);
const ES_LOCALE: Record<string, string> = { COP: "es-CO", MXN: "es-MX", ARS: "es-AR" };

function locale(lang: Language | "en", currency?: string | null): string {
  if (lang === "en") return "en-US";
  if (lang === "pt") return "pt-BR";
  return (currency && ES_LOCALE[currency]) || "es-419";
}

/**
 * "COP 48.900", "MXN 9,840.00". Unknown amounts return an empty string. Convention (the service writes reply text
 * the same way, agent/orchestrator/fmt.py; both are tested on docs/format-vectors.json): Spanish uses the number
 * format of the currency's country (COP and ARS 1.234,56; MXN, USD and others 1,234.56), Portuguese the Brazilian
 * one for every code; COP without decimals.
 */
export function money(amount: number | null, currency: string | null, lang: Language | "en"): string {
  if (amount === null || currency === null) return "";
  const digits = ZERO_DECIMALS.has(currency) ? 0 : 2;
  return new Intl.NumberFormat(locale(lang, currency), {
    style: "currency",
    currency,
    currencyDisplay: "code",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
    .format(amount)
    .replace(/ /g, " ");
}

/**
 * Transaction dates arrive without an offset (local time of the transaction). They are shown with the same
 * wall-clock digits, so they are parsed and formatted as UTC.
 */
function asUtc(value: string): Date {
  const hasOffset = /[zZ]|[+-]\d\d:?\d\d$/.test(value);
  return new Date(hasOffset ? value : value.length === 10 ? `${value}T00:00:00Z` : `${value}Z`);
}

export function dateOnly(value: string | null | undefined, lang: Language | "en"): string {
  if (!value) return "";
  return new Intl.DateTimeFormat(locale(lang), { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(asUtc(value));
}

/** Wall-clock "HH:MM" of a transaction timestamp as the service sent it (no offset, no conversion). */
export function wallTime(value: string | null | undefined): string {
  const match = /T(\d{2}:\d{2})/.exec(value ?? "");
  return match?.[1] ?? "";
}

export function timeOnly(value: string, lang: Language | "en", timeZone?: string): string {
  return new Intl.DateTimeFormat(locale(lang), { hour: "2-digit", minute: "2-digit", second: undefined, hour12: false, timeZone }).format(new Date(value));
}

/** Console and audit show UTC with the unit, e.g. "25 Sep 2026, 19:36:05 UTC". */
export function utcStamp(value: string, withSeconds = false): string {
  const text = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
    hour12: false,
    timeZone: "UTC",
  }).format(new Date(value));
  return `${text} UTC`;
}

export function ms(value: number): string {
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: value < 10 ? 1 : 0 }).format(value)} ms`;
}

export function shortHash(value: string | null, keep = 6): string {
  if (!value) return "";
  return value.length <= keep * 2 ? value : `${value.slice(0, keep)}…${value.slice(-4)}`;
}


export interface TxLabel {
  when: string;
  who: string;
  amount: number | null;
  currency: string | null;
  /** The amount exactly as the service wrote it, for when it cannot be parsed. */
  rawAmount: string;
}

/**
 * Service labels read "30/05/2026, Marketplace Uno, 5335.32 MXN" (agent/orchestrator/steps.py tx_label).
 * Returns null for any other shape, so the caller shows the label as sent.
 */
export function parseTxLabel(label: string): TxLabel | null {
  const parts = label.split(", ");
  if (parts.length < 3) return null;
  const rawAmount = parts[parts.length - 1] ?? "";
  const match = /^(-?\d+(?:\.\d+)?)\s+([A-Z]{3})$/.exec(rawAmount);
  return {
    when: parts[0] ?? "",
    who: parts.slice(1, -1).join(", "),
    amount: match ? Number(match[1]) : null,
    currency: match?.[2] ?? null,
    rawAmount,
  };
}

/** Date of a parsed label ("30/05/2026") in the customer's format, or the service's text when it is another shape. */
export function labelDate(label: TxLabel, lang: Language | "en"): string {
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(label.when);
  return match ? dateOnly(`${match[3]}-${match[2]}-${match[1]}`, lang) : label.when;
}

/** Amount of a parsed label in the customer's locale, or the service's text when it could not be parsed. */
export function labelAmount(label: TxLabel, lang: Language | "en"): string {
  return label.amount === null ? label.rawAmount : money(label.amount, label.currency, lang);
}

/** A service label as reply text reads it: "30 may 2026, Marketplace Uno, MXN 5,335.32" (agent/orchestrator/fmt.py). */
export function labelText(label: string, lang: Language): string {
  const parts = parseTxLabel(label);
  if (!parts || parts.amount === null || !/^\d{2}\/\d{2}\/\d{4}$/.test(parts.when)) return label;
  return `${labelDate(parts, lang)}, ${parts.who}, ${labelAmount(parts, lang)}`;
}

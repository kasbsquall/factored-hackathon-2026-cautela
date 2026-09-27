import {
  ArrowsLeftRight, Bank, Broadcast, CalendarBlank, CalendarCheck, Coins, CreditCard, Crosshair, DeviceMobile, Globe,
  HandPointing, Hash, MapPin, Money, Storefront, Tag, type Icon,
} from "@phosphor-icons/react";
import type { ChargeView, MatchCode, MatchReason } from "@/lib/api/types";
import type { CustomerCopy, UiLang } from "@/lib/i18n/customer";
import { dateOnly, money, wallTime } from "@/lib/format";
import { reasonLabelEn } from "@/lib/i18n/match-reasons";
import styles from "./charge-facts.module.css";

/** One icon per reason family, so the same kind of match reads the same everywhere. */
const REASON_ICON: Record<MatchCode, Icon> = {
  amount_exact: Coins,
  amount_close: Coins,
  date_same_day: CalendarCheck,
  date_within_days: CalendarCheck,
  date_in_range: CalendarCheck,
  date_near: CalendarCheck,
  merchant_named: Storefront,
  type_match: Tag,
  channel_match: Broadcast,
  city_match: MapPin,
  only_fit: Crosshair,
  customer_selected: HandPointing,
  customer_reference: Hash,
};

const CHANNEL_ICON: Record<string, Icon> = {
  App: DeviceMobile, Web: Globe, POS: Storefront, ATM: Money, Branch: Bank, Transfer: ArrowsLeftRight,
};

export function channelIcon(channel: string | null): Icon {
  return (channel && CHANNEL_ICON[channel]) || Broadcast;
}

/**
 * The match reasons the service computed from the ranker features that fired. Never inferred here. The reviewer view
 * (lang "en") words the same code and number in English; the customer view shows the service's own label.
 */
export function ReasonChips({ reasons, copy, lang }: { reasons: MatchReason[]; copy: CustomerCopy; lang: UiLang }) {
  if (!reasons.length) return <span className={styles.noReasons}>{copy.noReasons}</span>;
  return (
    <span className={styles.reasons} role="list" aria-label={copy.whyMatched}>
      {reasons.map((r) => {
        const ReasonIcon = REASON_ICON[r.code] ?? Tag;
        return (
          <span key={r.code} role="listitem" className={`${styles.reason} ${r.code === "only_fit" ? styles.decisive : ""}`}>
            <ReasonIcon aria-hidden />
            <span className="num">{lang === "en" ? reasonLabelEn(r) : r.label}</span>
          </span>
        );
      })}
    </span>
  );
}

/**
 * "Food · MCC 5411". Some sources put a category name in merchant_category instead of an MCC code ("Food"): a name
 * equal to the category is not repeated, and only a numeric code gets the MCC prefix.
 */
export function categoryText(category: string | null, merchantCategory: string | null, copy: CustomerCopy): string {
  const main = category ? copy.category[category] ?? category : null;
  const code = merchantCategory?.trim() || null;
  const extra = !code ? null
    : /^\d{3,4}$/.test(code) ? copy.mcc(code)
      : code.toLowerCase() === category?.toLowerCase() ? null
        : copy.category[code] ?? code;
  return [main, extra].filter(Boolean).join(" · ");
}

export function chargeAmount(charge: ChargeView, lang: UiLang): string {
  return money(charge.amount, charge.currency, lang) || "?";
}

export function chargeDay(charge: ChargeView, lang: UiLang): string {
  const time = wallTime(charge.transaction_date);
  return [dateOnly(charge.transaction_date, lang), time].filter(Boolean).join(" · ");
}

export function cardText(charge: ChargeView, copy: CustomerCopy): string | null {
  if (!charge.card_last4) return null;
  const kind = charge.card_type ? copy.cardType[charge.card_type] ?? charge.card_type : "";
  return `${kind} •••• ${charge.card_last4}`.trim();
}

/** One compact line under a candidate: when, how, where and with which card. */
export function ChargeMeta({ charge, copy, lang }: { charge: ChargeView; copy: CustomerCopy; lang: UiLang }) {
  const ChannelIcon = channelIcon(charge.channel);
  const card = cardText(charge, copy);
  return (
    <span className={styles.meta}>
      <span className={styles.metaItem}><CalendarBlank aria-hidden /><span className="num">{chargeDay(charge, lang)}</span></span>
      {charge.channel ? <span className={styles.metaItem}><ChannelIcon aria-hidden />{copy.channel[charge.channel] ?? charge.channel}</span> : null}
      {charge.city ? <span className={styles.metaItem}><MapPin aria-hidden />{charge.city}</span> : null}
      {card ? <span className={styles.metaItem}><CreditCard aria-hidden /><span className="num">{card}</span></span> : null}
    </span>
  );
}

import {
  ArrowsLeftRight, Bank, Broadcast, CalendarBlank, CalendarCheck, Coins, CreditCard, Crosshair, DeviceMobile, Globe,
  HandPointing, Hash, MapPin, Money, Storefront, Tag, type Icon,
} from "@phosphor-icons/react";
import type { ChargeView, Language, MatchCode, MatchReason } from "@/lib/api/types";
import type { CustomerCopy } from "@/lib/i18n/customer";
import { dateOnly, money, wallTime } from "@/lib/format";
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

/** The match reasons the service computed from the ranker features that fired. Never inferred here. */
export function ReasonChips({ reasons, copy }: { reasons: MatchReason[]; copy: CustomerCopy }) {
  if (!reasons.length) return <span className={styles.noReasons}>{copy.noReasons}</span>;
  return (
    <span className={styles.reasons} role="list" aria-label={copy.whyMatched}>
      {reasons.map((r) => {
        const ReasonIcon = REASON_ICON[r.code] ?? Tag;
        return (
          <span key={r.code} role="listitem" className={`${styles.reason} ${r.code === "only_fit" ? styles.decisive : ""}`}>
            <ReasonIcon aria-hidden />
            <span className="num">{r.label}</span>
          </span>
        );
      })}
    </span>
  );
}

export function chargeAmount(charge: ChargeView, lang: Language): string {
  return money(charge.amount, charge.currency, lang) || "?";
}

export function chargeDay(charge: ChargeView, lang: Language): string {
  const time = wallTime(charge.transaction_date);
  return [dateOnly(charge.transaction_date, lang), time].filter(Boolean).join(" · ");
}

export function cardText(charge: ChargeView, copy: CustomerCopy): string | null {
  if (!charge.card_last4) return null;
  const kind = charge.card_type ? copy.cardType[charge.card_type] ?? charge.card_type : "";
  return `${kind} •••• ${charge.card_last4}`.trim();
}

/** One compact line under a candidate: when, how, where and with which card. */
export function ChargeMeta({ charge, copy, lang }: { charge: ChargeView; copy: CustomerCopy; lang: Language }) {
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

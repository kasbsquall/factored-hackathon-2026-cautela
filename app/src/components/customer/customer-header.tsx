"use client";

import { Eyeglasses, Flask, LockSimple, SignOut } from "@phosphor-icons/react";
import { Wordmark } from "@/components/brand/wordmark";
import type { Language } from "@/lib/api/types";
import { LANGUAGE_LABEL, type CustomerCopy, type UiLang } from "@/lib/i18n/customer";
import { timeOnly } from "@/lib/format";
import styles from "./customer-header.module.css";

interface Props {
  copy: CustomerCopy;
  /** Conversation language. */
  lang: Language;
  /** Language of the labels: the conversation language, or English in the reviewer view. */
  ui: UiLang;
  onLang: (lang: Language) => void;
  reviewEn: boolean;
  onReview: () => void;
  expiresAt: string | null;
  expiringSoon: boolean;
  onLogout?: () => void;
}

const LANGS: Language[] = ["es", "pt"];

export function CustomerHeader({ copy, lang, ui, onLang, reviewEn, onReview, expiresAt, expiringSoon, onLogout }: Props) {
  return (
    <header className={styles.header}>
      <Wordmark size="sm" label={ui === "en" ? "Cautela, home" : lang === "pt" ? "Cautela, início" : "Cautela, inicio"} />
      <div className={styles.meta}>
        <span className={styles.badge}>
          <Flask aria-hidden />
          {copy.synthetic}
        </span>
        {expiresAt ? (
          <span className={`${styles.badge} ${expiringSoon ? styles.soon : ""} num`}>
            <LockSimple aria-hidden />
            {copy.testSession} · {copy.expires(timeOnly(expiresAt, ui))}
          </span>
        ) : null}
        {reviewEn ? (
          <span className={`${styles.badge} ${styles.reviewNote}`} lang="en">
            Reviewer view: labels in English, conversation in {LANGUAGE_LABEL[lang]}
          </span>
        ) : null}
        <span className="sr-only" role="status">{expiresAt && expiringSoon ? copy.expiringSoon : ""}</span>
      </div>
      <div className={styles.actions}>
        <div className={styles.langs} role="group" aria-label={copy.langSwitch}>
          {LANGS.map((l) => (
            <button key={l} type="button" className={styles.lang} aria-pressed={l === lang} onClick={() => onLang(l)} lang={l}>
              <span>{l.toUpperCase()}</span>
              <span className="sr-only"> {l === "es" ? "Español" : "Português"}</span>
            </button>
          ))}
        </div>
        <button type="button" className={styles.review} aria-pressed={reviewEn} onClick={onReview} lang="en"
          title="Reviewer view: show the labels in English; the conversation keeps its language">
          <Eyeglasses aria-hidden />
          <span>EN</span>
          <span className="sr-only"> reviewer view, labels in English</span>
        </button>
        {onLogout ? (
          <button type="button" className={styles.logout} onClick={onLogout}>
            <SignOut aria-hidden />
            <span>{copy.logout}</span>
          </button>
        ) : null}
      </div>
    </header>
  );
}

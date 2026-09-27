"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getApi } from "@/lib/api";
import type { Language } from "@/lib/api/types";
import type { TestIdentity } from "@/lib/api/client";
import { CUSTOMER_COPY, scenarioLanguage, type UiLang } from "@/lib/i18n/customer";
import type { TurnCause } from "@/lib/narration";
import { useDemoClock } from "@/lib/use-demo-clock";
import { CustomerHeader } from "./customer-header";
import { LoginPanel } from "./login-panel";
import { Conversation } from "./conversation";
import { ExpiredState } from "./expired-state";
import { NarrationPanel } from "./narration-panel";
import { clearTranslationCache } from "./message-translation";
import { Walkthrough } from "./walkthrough";
import type { TurnRecord } from "./flow-types";
import styles from "./customer.module.css";

export interface Auth {
  token: string;
  expiresAt: string;
  /** The test identity picked on the login screen, when one was picked (it carries the suggested messages). */
  identity: TestIdentity | null;
}

/** What the reviewer narration needs from the conversation. */
export interface TrailState {
  turns: TurnRecord[];
  pending: TurnCause | null;
  failed: boolean;
}

const NO_TRAIL: TrailState = { turns: [], pending: null, failed: false };

/** Warn this long before the session ends, so a customer mid-confirmation is not surprised. */
const EXPIRY_WARNING_MS = 60_000;
const REVIEW_KEY = "cautela.review-en";
const LANG_KEY = "cautela.lang";

/** ?review=en wins (a link for judges), then this browser's last choice. Storage can be missing or blocked. */
function initialReview(): boolean {
  try {
    const param = new URLSearchParams(window.location.search).get("review");
    if (param === "en") return true;
    if (param === "off") return false;
    return window.localStorage.getItem(REVIEW_KEY) === "on";
  } catch {
    return false;
  }
}

/**
 * This browser's last conversation language, so it survives a reload or a new login, and whether a test customer set
 * it ("pick") or the switch did. Stored as "pt" or "pt:pick".
 */
function initialLang(): { lang: Language; picked: boolean } {
  try {
    const [value, source] = (window.localStorage.getItem(LANG_KEY) ?? "").split(":");
    return { lang: value === "pt" ? "pt" : "es", picked: source === "pick" };
  } catch {
    return { lang: "es", picked: false };
  }
}

export function CustomerApp() {
  const [lang, setLang] = useState<Language>("es");
  const [reviewEn, setReviewEn] = useState(false);
  const [auth, setAuth] = useState<Auth | null>(null);
  const [expired, setExpired] = useState(false);
  const [stage, setStage] = useState(0);
  const [expiringSoon, setExpiringSoon] = useState(false);
  const [trail, setTrail] = useState<TrailState>(NO_TRAIL);
  /** Chrome (labels, cards, walkthrough) follows the reviewer toggle; the conversation always stays in `lang`. */
  const ui: UiLang = reviewEn ? "en" : lang;
  const copy = CUSTOMER_COPY[ui];
  const talk = CUSTOMER_COPY[lang];
  const demoClock = useDemoClock();

  /** True while the language was set by picking a test customer rather than by the switch. */
  const langFromPick = useRef(false);

  useEffect(() => {
    setReviewEn(initialReview());
    const saved = initialLang();
    langFromPick.current = saved.picked;
    setLang(saved.lang);
  }, []);

  useEffect(() => {
    document.documentElement.lang = ui === "en" ? "en" : ui === "pt" ? "pt-BR" : "es";
    document.title = `${copy.pageTitle} · Cautela`;
  }, [ui, copy]);

  const chooseLang = useCallback((next: Language, picked: boolean) => {
    langFromPick.current = picked;
    setLang(next);
    try {
      window.localStorage.setItem(LANG_KEY, picked ? `${next}:pick` : next);
    } catch {
      // Only a convenience: without storage the choice lasts for this page.
    }
  }, []);

  const switchLang = useCallback((next: Language) => chooseLang(next, false), [chooseLang]);

  // A scenario written for one language (the Portuguese customer) sets it; picking another customer afterwards goes
  // back to Spanish, unless the language came from the switch, which always wins.
  const pickIdentity = useCallback((identity: TestIdentity) => {
    const wanted = scenarioLanguage(identity.scenario);
    if (wanted) chooseLang(wanted, true);
    else if (langFromPick.current) chooseLang("es", false);
  }, [chooseLang]);

  const toggleReview = useCallback(() => {
    const next = !reviewEn;
    setReviewEn(next);
    try {
      window.localStorage.setItem(REVIEW_KEY, next ? "on" : "off");
    } catch {
      // Only a convenience: without storage the choice lasts for this page.
    }
  }, [reviewEn]);

  const endSession = useCallback(() => {
    clearTranslationCache();
    setAuth(null);
    setExpired(true);
    setStage(0);
    setTrail(NO_TRAIL);
  }, []);

  // The backend is authoritative (every call checks expiry); this timer only shows the state on time.
  useEffect(() => {
    if (!auth) return;
    const left = Date.parse(auth.expiresAt) - Date.now();
    setExpiringSoon(left <= EXPIRY_WARNING_MS);
    const warn = window.setTimeout(() => setExpiringSoon(true), Math.max(0, left - EXPIRY_WARNING_MS));
    const timer = window.setTimeout(endSession, Math.max(0, left));
    return () => {
      window.clearTimeout(warn);
      window.clearTimeout(timer);
    };
  }, [auth, endSession]);

  const logout = useCallback(() => {
    if (auth) void getApi().logout(auth.token);
    clearTranslationCache();
    setAuth(null);
    setStage(0);
    setTrail(NO_TRAIL);
  }, [auth]);

  return (
    <div className={styles.shell}>
      <a href="#conversation" className="skip-link">{ui === "en" ? "Skip to content" : ui === "pt" ? "Ir para o conteúdo" : "Ir al contenido"}</a>
      <CustomerHeader copy={copy} lang={lang} ui={ui} onLang={switchLang} reviewEn={reviewEn} onReview={toggleReview}
        expiresAt={auth?.expiresAt ?? null} expiringSoon={Boolean(auth) && expiringSoon} onLogout={auth ? logout : undefined} demoClock={demoClock} />
      <div className={styles.layout}>
        <main id="conversation" className={styles.phone}>
          {expired ? (
            <ExpiredState copy={copy} onAgain={() => setExpired(false)} />
          ) : auth ? (
            <Conversation key={auth.token} auth={auth} copy={copy} talk={talk} lang={lang} ui={ui} demoClock={demoClock} onSessionEnd={endSession}
              onStage={setStage} onTrail={setTrail} />
          ) : (
            <LoginPanel copy={copy} onPickIdentity={pickIdentity} onAuthenticated={(a) => { setAuth(a); setStage(1); setTrail(NO_TRAIL); }} />
          )}
        </main>
        <NarrationPanel className={styles.narration} turns={trail.turns} pending={trail.pending} failed={trail.failed} active={Boolean(auth)} />
        <Walkthrough className={styles.walk} copy={copy} stage={auth ? stage : 0} />
      </div>
    </div>
  );
}

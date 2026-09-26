"use client";

import { useCallback, useEffect, useState } from "react";
import { getApi } from "@/lib/api";
import type { Language } from "@/lib/api/types";
import type { TestIdentity } from "@/lib/api/client";
import { CUSTOMER_COPY, type UiLang } from "@/lib/i18n/customer";
import type { TurnCause } from "@/lib/narration";
import { CustomerHeader } from "./customer-header";
import { LoginPanel } from "./login-panel";
import { Conversation } from "./conversation";
import { ExpiredState } from "./expired-state";
import { NarrationPanel } from "./narration-panel";
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

  useEffect(() => setReviewEn(initialReview()), []);

  useEffect(() => {
    document.documentElement.lang = ui === "en" ? "en" : ui === "pt" ? "pt-BR" : "es";
  }, [ui]);

  const toggleReview = useCallback(() => {
    setReviewEn((on) => {
      try {
        window.localStorage.setItem(REVIEW_KEY, on ? "off" : "on");
      } catch {
        // Only a convenience: without storage the choice lasts for this page.
      }
      return !on;
    });
  }, []);

  const endSession = useCallback(() => {
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
    setAuth(null);
    setStage(0);
    setTrail(NO_TRAIL);
  }, [auth]);

  return (
    <div className={styles.shell}>
      <a href="#conversation" className="skip-link">{ui === "en" ? "Skip to content" : ui === "pt" ? "Ir para o conteúdo" : "Ir al contenido"}</a>
      <CustomerHeader copy={copy} lang={lang} ui={ui} onLang={setLang} reviewEn={reviewEn} onReview={toggleReview}
        expiresAt={auth?.expiresAt ?? null} expiringSoon={Boolean(auth) && expiringSoon} onLogout={auth ? logout : undefined} />
      <div className={styles.layout}>
        <main id="conversation" className={styles.phone}>
          {expired ? (
            <ExpiredState copy={copy} onAgain={() => setExpired(false)} />
          ) : auth ? (
            <Conversation key={auth.token} auth={auth} copy={copy} talk={talk} lang={lang} ui={ui} onSessionEnd={endSession}
              onStage={setStage} onTrail={setTrail} />
          ) : (
            <LoginPanel copy={copy} onAuthenticated={(a) => { setAuth(a); setStage(1); setTrail(NO_TRAIL); }} />
          )}
        </main>
        <NarrationPanel className={styles.narration} turns={trail.turns} pending={trail.pending} failed={trail.failed} active={Boolean(auth)} />
        <Walkthrough className={styles.walk} copy={copy} stage={auth ? stage : 0} />
      </div>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";
import { getApi } from "@/lib/api";
import type { Language } from "@/lib/api/types";
import type { TestIdentity } from "@/lib/api/client";
import { CUSTOMER_COPY } from "@/lib/i18n/customer";
import { CustomerHeader } from "./customer-header";
import { LoginPanel } from "./login-panel";
import { Conversation } from "./conversation";
import { ExpiredState } from "./expired-state";
import { Walkthrough } from "./walkthrough";
import styles from "./customer.module.css";

export interface Auth {
  token: string;
  expiresAt: string;
  /** The test identity picked on the login screen, when one was picked (it carries the suggested messages). */
  identity: TestIdentity | null;
}

/** Warn this long before the session ends, so a customer mid-confirmation is not surprised. */
const EXPIRY_WARNING_MS = 60_000;

export function CustomerApp() {
  const [lang, setLang] = useState<Language>("es");
  const [auth, setAuth] = useState<Auth | null>(null);
  const [expired, setExpired] = useState(false);
  const [stage, setStage] = useState(0);
  const [expiringSoon, setExpiringSoon] = useState(false);
  const copy = CUSTOMER_COPY[lang];

  useEffect(() => {
    document.documentElement.lang = lang === "pt" ? "pt-BR" : "es";
  }, [lang]);

  const endSession = useCallback(() => {
    setAuth(null);
    setExpired(true);
    setStage(0);
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
  }, [auth]);

  return (
    <div className={styles.shell}>
      <a href="#conversation" className="skip-link">{lang === "pt" ? "Ir para o conteúdo" : "Ir al contenido"}</a>
      <CustomerHeader copy={copy} lang={lang} onLang={setLang} expiresAt={auth?.expiresAt ?? null} expiringSoon={Boolean(auth) && expiringSoon} onLogout={auth ? logout : undefined} />
      <div className={styles.layout}>
        <main id="conversation" className={styles.phone}>
          {expired ? (
            <ExpiredState copy={copy} onAgain={() => setExpired(false)} />
          ) : auth ? (
            <Conversation key={auth.token} auth={auth} copy={copy} lang={lang} onSessionEnd={endSession} onStage={setStage} />
          ) : (
            <LoginPanel copy={copy} onAuthenticated={(a) => { setAuth(a); setStage(1); }} />
          )}
        </main>
        <Walkthrough copy={copy} stage={auth ? stage : 0} />
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { ArrowRight, EnvelopeSimple, IdentificationCard } from "@phosphor-icons/react";
import { ApiError, getApi, type TestIdentity } from "@/lib/api";
import type { LoginChallenge } from "@/lib/api/types";
import type { CustomerCopy } from "@/lib/i18n/customer";
import { Button } from "@/components/ui/button";
import type { Auth } from "./customer-app";
import styles from "./login-panel.module.css";

interface Props {
  copy: CustomerCopy;
  onAuthenticated: (auth: Auth) => void;
}

type OtpError = keyof CustomerCopy["errOtp"];

export function LoginPanel({ copy, onAuthenticated }: Props) {
  const api = getApi();
  // undefined while loading, null when the list could not be read.
  const [identities, setIdentities] = useState<TestIdentity[] | null | undefined>(undefined);
  const [attempt, setAttempt] = useState(0);
  const [doc, setDoc] = useState("");
  const [challenge, setChallenge] = useState<LoginChallenge | null>(null);
  const [outbox, setOutbox] = useState<string | null | undefined>(undefined);
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setIdentities(undefined);
    api.listTestIdentities().then((list) => live && setIdentities(list)).catch(() => live && setIdentities(null));
    return () => { live = false; };
  }, [api, attempt]);

  const identity = identities?.find((i) => i.document === doc.replace(/\D/g, "")) ?? null;

  async function loadOutbox(challengeId: string) {
    setOutbox(undefined);
    setOutbox(await api.readTestOutbox(challengeId).catch(() => null));
  }

  async function requestCode(event: React.FormEvent) {
    event.preventDefault();
    if (pending) return;
    if (!doc.trim()) {
      setError(copy.errDocument);
      return;
    }
    setPending(true);
    setError(null);
    try {
      const next = await api.startLogin(doc.trim());
      setChallenge(next);
      await loadOutbox(next.challenge_id);
    } catch (err) {
      setError(err instanceof ApiError && err.code === "rate_limited" ? copy.errOtp.rate_limited : copy.errConnect);
    } finally {
      setPending(false);
    }
  }

  async function verify(event: React.FormEvent) {
    event.preventDefault();
    if (!challenge || pending) return;
    setPending(true);
    setError(null);
    try {
      const grant = await api.verifyOtp(challenge.challenge_id, code);
      onAuthenticated({ token: grant.token, expiresAt: grant.expires_at, identity });
    } catch (err) {
      const known = err instanceof ApiError && err.code in copy.errOtp ? (err.code as OtpError) : null;
      setError(known ? copy.errOtp[known] : copy.errConnect);
      setPending(false);
    }
  }

  return (
    <div className={styles.panel}>
      <div className={`${styles.intro} rise`}>
        <h1 className={styles.title}>{copy.loginTitle}</h1>
        <p className={styles.lede}>{copy.loginLede}</p>
      </div>

      <aside className={`${styles.test} rise`} style={{ "--i": 1 } as React.CSSProperties} aria-labelledby="test-title">
        <IdentificationCard aria-hidden />
        <div>
          <p id="test-title" className={styles.testTitle}>{copy.testTitle}</p>
          <p className={styles.testBody}>{copy.testBody}</p>
        </div>
      </aside>

      {!challenge ? (
        <form className={`${styles.form} rise`} style={{ "--i": 2 } as React.CSSProperties} onSubmit={requestCode} noValidate>
          <label className={styles.label} htmlFor="document">{copy.documentLabel}</label>
          <input
            id="document"
            className={`${styles.input} mono`}
            inputMode="numeric"
            autoComplete="off"
            value={doc}
            onChange={(e) => setDoc(e.target.value)}
            aria-describedby="document-help document-error"
            aria-invalid={Boolean(error) || undefined}
          />
          <p id="document-help" className={styles.help}>{copy.documentHelp}</p>
          <fieldset className={styles.identities}>
            <legend className="eyebrow">{copy.pickIdentity}</legend>
            {identities === undefined ? (
              [0, 1, 2, 3].map((i) => <span key={i} className={`skeleton ${styles.idSkeleton}`} />)
            ) : identities === null ? (
              <div className={styles.idError}>
                <p className={styles.help}>{copy.identitiesFail}</p>
                <Button variant="secondary" size="sm" onClick={() => setAttempt((n) => n + 1)}>{copy.retry}</Button>
              </div>
            ) : (
              identities.map((id, i) => (
                  <button
                    key={id.document}
                    type="button"
                    className={`${styles.identity} rise-row`}
                    style={{ "--i": Math.min(i, 7) } as React.CSSProperties}
                    aria-pressed={doc === id.document}
                    onClick={() => { setDoc(id.document); setError(null); }}
                  >
                    <span className={styles.idName}>{copy.scenario[id.scenario] ?? id.label}</span>
                    <span className={`${styles.idDoc} mono`}>{id.document}</span>
                    {id.person ? <span className={styles.idScenario}>{id.person}</span> : null}
                  </button>
                ))
            )}
          </fieldset>
          <p id="document-error" className={styles.error} role="alert">{error}</p>
          <Button type="submit" loading={pending} loadingLabel={copy.sending} trailing={<ArrowRight aria-hidden />}>
            {copy.sendCode}
          </Button>
        </form>
      ) : (
        <form className={`${styles.form} rise`} onSubmit={verify} noValidate>
          <div className={styles.outbox} aria-live="polite">
            <EnvelopeSimple aria-hidden />
            <div>
              <p className="eyebrow">{copy.outboxTitle}</p>
              {outbox === undefined ? (
                <span className={`skeleton ${styles.codeSkeleton}`} />
              ) : outbox ? (
                <p className={styles.outboxCode}>
                  <span>{copy.outboxReceived}</span>
                  <span className="mono">{outbox.replace(/(\d{3})(\d{3})/, "$1 $2")}</span>
                </p>
              ) : (
                <p className={styles.help}>
                  {copy.outboxEmpty}{" "}
                  <button type="button" className={styles.inlineLink} onClick={() => void loadOutbox(challenge.challenge_id)}>{copy.checkAgain}</button>
                </p>
              )}
            </div>
            {outbox ? (
              <Button variant="secondary" size="sm" onClick={() => setCode(outbox)}>{copy.useCode}</Button>
            ) : null}
          </div>
          <label className={styles.label} htmlFor="otp">{copy.otpLabel}</label>
          <input
            id="otp"
            className={`${styles.input} ${styles.otp} mono`}
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            aria-describedby="otp-help otp-error"
            aria-invalid={Boolean(error) || undefined}
          />
          <p id="otp-help" className={styles.help}>{copy.otpHelp}</p>
          <p id="otp-error" className={styles.error} role="alert">{error}</p>
          <div className={styles.row}>
            <Button type="submit" loading={pending} loadingLabel={copy.verifying} disabled={code.length !== 6} trailing={<ArrowRight aria-hidden />}>
              {copy.enter}
            </Button>
            <Button variant="ghost" onClick={() => { setChallenge(null); setCode(""); setError(null); }}>
              {copy.changeDocument}
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

"use client";

import { useEffect, useRef } from "react";
import { ArrowRight, ClockCountdown } from "@phosphor-icons/react";
import type { CustomerCopy } from "@/lib/i18n/customer";
import { Button } from "@/components/ui/button";
import styles from "./expired-state.module.css";

export function ExpiredState({ copy, onAgain }: { copy: CustomerCopy; onAgain: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  // The session can end mid-conversation, far down the page: bring the reader to the explanation.
  useEffect(() => {
    window.scrollTo({ top: 0 });
    heading.current?.focus();
  }, []);
  return (
    <div className={`${styles.state} rise`}>
      <span className={styles.icon}><ClockCountdown aria-hidden /></span>
      <h1 ref={heading} tabIndex={-1} className={styles.title}>{copy.expiredTitle}</h1>
      <p className={styles.body}>{copy.expiredBody}</p>
      <Button onClick={onAgain} trailing={<ArrowRight aria-hidden />}>{copy.expiredAction}</Button>
    </div>
  );
}

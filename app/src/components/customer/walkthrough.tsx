"use client";

import Link from "next/link";
import { ArrowUpRight, Check, HandTap, IdentificationCard, ListMagnifyingGlass, Receipt } from "@phosphor-icons/react";
import type { CustomerCopy } from "@/lib/i18n/customer";
import styles from "./walkthrough.module.css";

const ICONS = [IdentificationCard, ListMagnifyingGlass, HandTap, Receipt];

export function Walkthrough({ copy, stage }: { copy: CustomerCopy; stage: number }) {
  return (
    <aside className={`${styles.aside} rise`} style={{ "--i": 2 } as React.CSSProperties} aria-labelledby="walk-title">
      <h2 id="walk-title" className={styles.title}>{copy.walkTitle}</h2>
      <p className={styles.lede}>{copy.walkLede}</p>
      <ol className={styles.steps}>
        {copy.walk.map(([name, detail], i) => {
          const Icon = ICONS[i] ?? Receipt;
          const state = i < stage ? "done" : i === stage ? "now" : "next";
          return (
            <li key={name} className={`${styles.step} ${styles[state]}`} aria-current={state === "now" ? "step" : undefined}>
              <span className={styles.marker} aria-hidden>
                {state === "done" ? <Check /> : <Icon />}
              </span>
              <span className={styles.text}>
                <span className={styles.name}>
                  <span className="mono">{i + 1}</span> {name}
                  {state !== "next" ? <span className={styles.state}>{state === "done" ? copy.stepDone : copy.stepNow}</span> : null}
                </span>
                <span className={styles.detail}>{detail}</span>
              </span>
            </li>
          );
        })}
      </ol>
      <nav className={styles.links} aria-label={copy.walkTitle}>
        <Link href="/console">{copy.walkConsole}<ArrowUpRight aria-hidden /></Link>
        <Link href="/audit">{copy.walkAudit}<ArrowUpRight aria-hidden /></Link>
      </nav>
    </aside>
  );
}

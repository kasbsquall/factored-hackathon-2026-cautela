"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CalendarDot, Flask } from "@phosphor-icons/react";
import { Wordmark } from "@/components/brand/wordmark";
import { utcStamp } from "@/lib/format";
import { useDemoClock } from "@/lib/use-demo-clock";
import styles from "./ops-header.module.css";

/** `short` replaces the label on phones, so the four surfaces fit one row. */
const LINKS = [
  { href: "/console", label: "Agent console", short: "Console" },
  { href: "/audit", label: "Audit trail", short: "Audit" },
  { href: "/customer", label: "Customer view", short: "Customer" },
  { href: "/insights", label: "Insights", short: "Insights" },
];

/** Surfaces whose times and dates come from the service clock. */
const CLOCKED = ["/console", "/audit"];
const DEMO_NOTE = "The service runs on a demo date, the date the test data is set at. Handoff times, check dates and claim deadlines follow it.";

export function OpsHeader() {
  const path = usePathname();
  const clock = useDemoClock();
  const demoDate = clock && CLOCKED.some((p) => path.startsWith(p)) ? utcStamp(clock).split(",")[0] : null;
  return (
    <>
      <a href="#main" className="skip-link">Skip to content</a>
      <header className={styles.header}>
        <Wordmark size="sm" />
        <nav className={styles.nav} aria-label="Surfaces">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} className={styles.link} aria-current={path.startsWith(l.href) ? "page" : undefined}>
              <span className={l.short !== l.label ? styles.long : undefined}>{l.label}</span>
              {l.short !== l.label ? <span className={styles.short}>{l.short}</span> : null}
            </Link>
          ))}
        </nav>
        <span className={styles.badges}>
          {demoDate ? (
            <span className={`${styles.badge} ${styles.demoDate} num`} title={DEMO_NOTE}>
              <CalendarDot aria-hidden />
              Demo date: {demoDate}
              <span className="sr-only">. {DEMO_NOTE}</span>
            </span>
          ) : null}
          <span className={styles.badge}>
            <Flask aria-hidden />
            Synthetic data
          </span>
        </span>
      </header>
    </>
  );
}

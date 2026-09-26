"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Flask } from "@phosphor-icons/react";
import { Wordmark } from "@/components/brand/wordmark";
import styles from "./ops-header.module.css";

const LINKS = [
  { href: "/console", label: "Agent console" },
  { href: "/audit", label: "Audit trail" },
  { href: "/customer", label: "Customer view" },
  { href: "/insights", label: "Insights" },
];

export function OpsHeader() {
  const path = usePathname();
  return (
    <>
      <a href="#main" className="skip-link">Skip to content</a>
      <header className={styles.header}>
        <Wordmark size="sm" />
        <nav className={styles.nav} aria-label="Surfaces">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} className={styles.link} aria-current={path.startsWith(l.href) ? "page" : undefined}>
              {l.label}
            </Link>
          ))}
        </nav>
        <span className={styles.badge}>
          <Flask aria-hidden />
          Synthetic data
        </span>
      </header>
    </>
  );
}

"use client";

import type { ReactNode } from "react";
import styles from "./notice.module.css";

interface NoticeProps {
  tone: "empty" | "error" | "info";
  icon: ReactNode;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
}

/** Empty, error and info states share a shape but not a tone, so they never read as the same thing. */
export function Notice({ tone, icon, title, children, actions }: NoticeProps) {
  return (
    <div className={`${styles.notice} ${styles[tone]}`} role={tone === "error" ? "alert" : "status"}>
      <span className={styles.icon}>{icon}</span>
      <div className={styles.body}>
        <p className={styles.title}>{title}</p>
        {children ? <div className={styles.text}>{children}</div> : null}
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
    </div>
  );
}

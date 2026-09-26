"use client";

import { HandTap, Prohibit, SealCheck, UserSwitch, WarningOctagon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import styles from "./status-chip.module.css";

export type StatusKind = "ok" | "cf" | "stop" | "bad" | "hu";

const ICON: Record<StatusKind, ReactNode> = {
  ok: <SealCheck aria-hidden />,
  cf: <HandTap aria-hidden />,
  stop: <Prohibit aria-hidden />,
  bad: <WarningOctagon aria-hidden />,
  hu: <UserSwitch aria-hidden />,
};

/** Status is never color alone: every kind has its own icon and a text label. */
export function StatusChip({ kind, children, size = "md" }: { kind: StatusKind; children: ReactNode; size?: "md" | "sm" }) {
  return (
    <span className={`${styles.chip} ${styles[kind]} ${styles[size]}`}>
      {ICON[kind]}
      <span>{children}</span>
    </span>
  );
}

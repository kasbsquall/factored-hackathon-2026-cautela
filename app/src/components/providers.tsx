"use client";

import { IconContext } from "@phosphor-icons/react";

const ICONS = { weight: "light" as const, size: 18, mirrored: false };

export function Providers({ children }: { children: React.ReactNode }) {
  return <IconContext.Provider value={ICONS}>{children}</IconContext.Provider>;
}

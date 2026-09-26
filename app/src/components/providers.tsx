"use client";

import { useEffect } from "react";
import { IconContext } from "@phosphor-icons/react";

const ICONS = { weight: "light" as const, size: 18, mirrored: false };

/**
 * Entrance animations play once and then leave the element at rest.
 *
 * A CSS animation restarts whenever its element goes from display:none back to visible. A full-page capture
 * resizes the viewport for a moment, which flips the console's responsive panes, so every section of the ready file
 * replayed its entrance (with 60 ms staggered delays, starting at opacity 0) exactly when the capture was taken:
 * the file looked empty. Once an entrance ends, the element is marked settled and CSS drops the animation, so a
 * later layout change cannot hide it again. A page that loads in a hidden tab gets no entrance at all, because its
 * animation timeline does not run until the tab is shown.
 */
function useSettledEntrances(): void {
  useEffect(() => {
    const root = document.documentElement;
    if (document.visibilityState === "hidden") root.dataset.entrance = "off";
    const settle = (event: AnimationEvent) => {
      if (event.target instanceof HTMLElement || event.target instanceof SVGElement) event.target.dataset.settled = "";
    };
    document.addEventListener("animationend", settle);
    return () => document.removeEventListener("animationend", settle);
  }, []);
}

export function Providers({ children }: { children: React.ReactNode }) {
  useSettledEntrances();
  return <IconContext.Provider value={ICONS}>{children}</IconContext.Provider>;
}

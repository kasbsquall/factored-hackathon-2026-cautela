"use client";

import { useEffect, useState } from "react";
import { getApi } from "@/lib/api";

/**
 * The service's demo clock (ISO instant), or null while it loads, when it cannot be read, and in mock mode.
 * Callers show nothing until it arrives: a demo date must never be a guessed default.
 */
export function useDemoClock(): string | null {
  const [clock, setClock] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    getApi().demoClock().then((value) => live && setClock(value)).catch(() => undefined);
    return () => { live = false; };
  }, []);
  return clock;
}

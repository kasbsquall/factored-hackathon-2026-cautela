import type { CautelaApi } from "./client";
import { LiveCautelaApi } from "./live-api";
import { MockCautelaApi } from "./mock/mock-api";

export * from "./client";
export type * from "./types";

let instance: CautelaApi | null = null;

/**
 * NEXT_PUBLIC_API_MODE=mock|live (default mock). Live calls NEXT_PUBLIC_API_BASE_URL, which defaults to the
 * same-origin proxy at /api/cautela (see src/app/api/cautela/[...path]/route.ts).
 */
export function getApi(): CautelaApi {
  if (instance) return instance;
  const mode = process.env.NEXT_PUBLIC_API_MODE === "live" ? "live" : "mock";
  instance = mode === "live"
    ? new LiveCautelaApi((process.env.NEXT_PUBLIC_API_BASE_URL || "/api/cautela").replace(/\/$/, ""))
    : new MockCautelaApi();
  return instance;
}

/** Tests inject their own instance (for example a mock with zero latency). */
export function setApiForTests(api: CautelaApi | null): void {
  instance = api;
}

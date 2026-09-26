"use client";

import { IdentificationCard, MagnifyingGlass, PencilSimpleLine, Scales, SealCheck, UserSwitch } from "@phosphor-icons/react";
import type { Phase } from "@/lib/audit";

const ICONS = {
  session: IdentificationCard,
  understand: MagnifyingGlass,
  decide: Scales,
  act: PencilSimpleLine,
  verify: SealCheck,
  escalate: UserSwitch,
} satisfies Record<Phase, unknown>;

export function PhaseIcon({ phase }: { phase: Phase }) {
  const Icon = ICONS[phase];
  return <Icon aria-hidden />;
}

# Cautela design direction

Written on day 1, before any styles; the typography and the stack below were updated to match `app/`. The UI build follows the owner's ui-workflow: design skills are loaded first, and the render is verified in a browser with audits for interaction, wording and pixel accuracy.

## Purpose

Show, in one glance, what the system verified, what it did, and why it stopped. The UI is the proof surface for the evaluation criteria. It is not decoration around a chatbot.

## Audiences and what they scan first

| Surface | Who | Scans first |
|---|---|---|
| Customer conversation | Bank customer in Spanish or Portuguese | Is my dispute registered, what happens next, by when |
| Human-agent console | Contact-center agent receiving a handoff | Why was this transferred, what is already verified, what is still open |
| Audit view | Judge, risk officer, engineer | The trace for one case: rules fired, tool calls, verification result, latency and cost |

The judges use all three. The first thing a judge sees must be a working case they can run in under 60 seconds, without an account. That means seeded synthetic customers and a trusted test session.

## Tone

Institutional ledger: calm, dense and exact, like a well-kept bank record book and not a consumer fintech ad. The agent console and the audit view are daily tools, so they stay quiet and scannable. The customer view can be warmer, but never playful about money.

## Memorable detail

**Verification receipts.** Every action the system claims appears as a receipt stub with the tool, the record id, and a "read back" check. When the system does not act, it prints a matching stub that names the rule that stopped it. Acting and abstaining get the same visual weight, which is the whole thesis of Cautela.

## Visual rules (from the owner's ui-workflow)

- **Typography.**
  - Display: Bricolage Grotesque. Body and UI: Atkinson Hyperlegible Next. Data, ids and amounts: IBM Plex Mono. All three are OFL, loaded through `next/font/google` (`app/src/app/layout.tsx`).
  - No Inter, Roboto or system fonts.
  - Numbers are always `tabular-nums lining-nums slashed-zero`.
- **Color.**
  - One warm-tinted gray family, never pure black or pure white.
  - One semantic accent, under 5% of pixels.
  - Status colors only for verified, needs confirmation, stopped and failed.
  - No purple or blue gradients.
- **Layout.**
  - No colored left-border highlights and no rows of three equal cards.
  - Radius 0 to 4px for data surfaces. Dividers at 1px with 6 to 10% alpha.
  - Spacing ratio of at least 1:20 between the smallest and the largest interval.
- **Icons.** Phosphor Light only, one weight and one base size. They appear on statuses, rule types, tool calls and handoff sections. No emojis anywhere.
- **Motion.**
  - Under 300ms, transform and opacity only.
  - Receipts stagger in by row (30ms, capped at 8), and the verification check draws on.
  - `prefers-reduced-motion` is covered.
- **States.** Every interactive element has default, hover, focus-visible, active, disabled and loading. Loading uses skeletons, never default values.

## Language

The UI copy is in Spanish and Portuguese for the customer surface, and in English for the agent and audit surfaces (the judges' working language). All labels follow the owner's writing guide.

## Stack

Next.js App Router, TypeScript, CSS Modules and Phosphor icons (`app/package.json`). The surfaces are `/customer`, `/console`, `/audit` and `/insights`.

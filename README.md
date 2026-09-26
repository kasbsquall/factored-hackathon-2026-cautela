# Cautela

**A transaction-dispute intake service for LATAM banks that resolves what it can verify, asks when it is unsure, and hands the rest to a human with the facts already checked.**

Factored AI & Data Hackathon 2026. Status: work in progress (day 1).

> *Cautela* means "caution" in both Spanish and Portuguese. The system is built around one idea from the challenge brief: AI should not be autonomous just because it can be.

## Why this workflow

To be completed with evidence from the supplied LATAM Bank dataset (contact reasons, complaint categories, SLA breaches, demand patterns). See `data_analytics/`.

## What it does

| Case | Behavior |
|---|---|
| Normal | Identifies the disputed transaction, checks eligibility against deterministic policy, performs the permitted action through a mock banking tool, then re-reads state to verify the outcome before reporting it. |
| Ambiguous or unsupported | Asks a targeted clarifying question, or abstains with a reason. |
| Human required | Transfers a structured handoff (request, verified facts, actions taken, evidence, open questions). No raw transcript dump. |

Languages: Spanish (from the supplied data) and Portuguese (team-generated, labeled as such).

## Architecture

See [docs/architecture.md](docs/architecture.md).

## Repository layout

| Folder | Discipline | Contents |
|---|---|---|
| `data_engineering/` | Data Engineering | Bronze/silver/gold pipeline, data contracts, quality checks, lineage |
| `data_analytics/` | Data Analytics | Workflow selection evidence, demand patterns, cost-per-resolution |
| `ml/` | Machine Learning | Baseline and learned decision component, experiment tracking |
| `agent/` | AI Engineering | Service, tools with contracts, policy engine, handoff |
| `app/` | AI Engineering | Customer and human-agent interfaces |
| `eval/` | All | Held-out cases and the evaluation runner |

## How to run

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
make fixture pipeline test   # synthetic test fixture -> bronze/silver warehouse -> tests
make pipeline-s3             # real data; settings in .env (see .env.example)
```

Without `make`, the equivalent commands are in the [Makefile](Makefile). Data engineering details: [data_engineering/README.md](data_engineering/README.md). Evaluation and app: to be completed.

## Evaluation

To be completed. Metrics follow the problem statement definitions: safe automated resolution, containment, escalation quality, unsafe outcomes (with counts and denominators), p50/p95 latency, and cost per attempted case and per successful resolution.

## Data provenance

| Input | Type |
|---|---|
| LATAM Bank dataset v1.0.0 (organizer-supplied) | Synthetic |
| Portuguese conversations | Team-generated |
| Adversarial test cases | Team-generated |

## Limitations and route to production

To be completed honestly as the build progresses.

## Frontend

`app/` is a Next.js App Router project (TypeScript, CSS Modules) with three surfaces: the customer conversation
(Spanish and Portuguese, mobile first), the agent console (handoff queue and ready file) and the audit trail (one
trace per conversation with its hash-chain status). Customer text is in the customer's language; the console and
audit pages are in English and show customer content as received.

```bash
cd app
npm install
npm run dev          # http://localhost:3100, mock mode by default
npm test             # Vitest component and unit tests
npm run e2e          # Playwright smoke of the customer flow (mock mode, reuses the dev server)
npm run gen:api      # regenerate src/lib/api/generated/openapi.d.ts from docs/schemas/openapi.json
```

Both modes implement one client contract (`app/src/lib/api/client.ts`) that mirrors the service: login with a
one-time code, then `POST /conversations/turn`, `/conversations/{id}/recognize` and `/conversations/{id}/confirm`,
`GET /cases/{id}` for the read-back, and the console reads. When several charges fit, each candidate card shows the
charge as the tools returned it (date, amount, merchant, channel, city, masked card) and chips for the ranker
features that actually matched; the customer always picks, or answers "none of these" and reaches a person. Before
any dispute, a "¿Lo reconoces?" card shows the merchant evidence: "I recognize it" ends without a dispute and
writes nothing, "I don't" goes on to the confirmation, which carries the computed claim deadline. **Mock mode** (`NEXT_PUBLIC_API_MODE=mock`, the default) runs that contract in
the browser with synthetic fixtures and the reply templates of `agent/orchestrator/replies.py`: four test
identities (several similar charges, amount review, a failing case store, a 40-second session), a one-time code
shown in a simulated channel, a real SHA-256 hash chain per trace and one deliberately tampered trace. State
resets on reload.

**Live mode** talks to the service in `api/` through a same-origin proxy (`src/app/api/cautela/[...path]/route.ts`),
so no CORS setup is needed and the console key never reaches the browser. Copy `app/.env.example` to
`app/.env.local` and set `NEXT_PUBLIC_API_MODE=live` and `CAUTELA_API_URL` (for example `http://127.0.0.1:8000`).
Console pages also need `CAUTELA_CONSOLE_PROXY=enabled` and `CAUTELA_CONSOLE_KEY` (in demo mode the service
writes a generated key to `data/demo/console_key.txt`); enable that only on a
deployment that is itself restricted to bank staff, because the proxy grants console access to whoever can reach it.

Entrance animations play once: when one ends, the element is marked settled and CSS drops the animation
(`app/src/components/providers.tsx`). Without that, a viewport change that toggles a responsive pane (a full-page
screenshot does this) replayed every entrance from opacity 0, and the console's ready file looked empty.

Known gaps: in live mode `/audit` lists only the traces behind queued handoffs (there is no trace listing endpoint)
and the chain status does not say where a chain breaks. The demo service runs its own clock: session expiry uses
`expires_in`, and the client moves confirmation expiry onto the browser clock with `GET /health`. Charges on a
non-card product (a loan, for example) show no card digits, and some gold rows have no merchant category.

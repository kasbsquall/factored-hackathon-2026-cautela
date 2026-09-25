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

To be completed. Target: one command to reproduce the pipeline, the evaluation, and the app.

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

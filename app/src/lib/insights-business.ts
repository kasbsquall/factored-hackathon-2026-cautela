/**
 * The business figures on /insights, read from src/reports/insights/insights.json (a copy of
 * data_analytics/reports/insights.json, made by scripts/copy-insights.mjs). This module selects fields and gives
 * them English labels; it computes nothing the report does not already hold.
 */
import report from "@/reports/insights/insights.json";
import { reasonLabel } from "@/lib/insights";

export const BUSINESS_SOURCE = "data_analytics/reports/insights.json";
export const BUSINESS_PROVENANCE = report.provenance;

const usdFmt = (digits: number) => new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
/** "USD 1,234", "USD 0.000581". */
export const usd = (value: number, digits = 0) => `USD ${usdFmt(digits).format(value)}`;
/** One decimal with grouping: 4,696.2 */
export const dec = (value: number, digits = 1) => usdFmt(digits).format(value);

export const WEEKDAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const slot = (c: { iso_dow: number; hour_of_day: number }) =>
  `${WEEKDAY_LONG[c.iso_dow - 1] ?? c.iso_dow} ${String(c.hour_of_day).padStart(2, "0")}:00`;

/* ---------- 1. workflow ranking ---------- */
const ranking = report.workflow_ranking;
export const RANKING = {
  rows: ranking.rows.map((r) => ({
    ...r,
    chosen: r.rank === ranking.chosen_rank,
    // The data names contact reasons as delivered; the page is English.
    proxy: r.proxy.replace("Retención", "Retention").replace("Cargo no reconocido complaints", "unrecognized-charge complaints (Cargo no reconocido)"),
    workflow: r.workflow.replace(" (unrecognized charge, the chosen workflow)", " (unrecognized charge)"),
  })),
  chosenRank: ranking.chosen_rank,
  candidates: ranking.candidates,
  judgment: new Set(ranking.judgment_columns),
  contactCenterHours: ranking.contact_center_agent_hours_per_year,
  coverage: ranking.dispute_record_coverage,
};
const chosen = RANKING.rows.find((r) => r.chosen);
if (!chosen) throw new Error("insights.json has no chosen workflow row");
export const CHOSEN = chosen;

/* ---------- 2. cost per resolution ---------- */
const cost = report.cost_per_resolution;
const byHour = (m: Record<string, number>) =>
  Object.entries(m).map(([hourly, value]) => ({ hourly: Number(hourly), value })).sort((a, b) => a.hourly - b.hourly);
export const COST = {
  scenario: cost.scenario,
  humanMinutes: cost.human_minutes_per_dispute,
  human: byHour(cost.human_usd_per_dispute_by_hourly_cost),
  withBot: byHour(cost.cost_per_routed_dispute_with_bot_by_hourly_cost),
  llmPerSafe: cost.llm_usd_per_safe_automated_resolution,
  llmPerConversation: cost.llm_usd_per_conversation,
  rate: cost.rate,
  hosting: cost.hosting,
  units: cost.units,
};

/* ---------- 3. projection range ---------- */
const projection = report.projection_range;
export const PROJECTION = {
  hourlyCost: projection.hourly_cost_usd,
  conservative: projection.headline.conservative_chat_only,
  optimistic: projection.headline.optimistic_with_call_center,
  rows: projection.rows,
  units: projection.units,
};
/** "App, Web and call-center disputes (assumption: ...)" -> the reach name and whether it is an assumption. */
export function reachLabel(reach: string): { name: string; assumption: boolean } {
  const assumption = /assumption/i.test(reach);
  return { name: reach.replace(/\s*\(.*\)$/, ""), assumption };
}

/* ---------- 4. handoff queue ---------- */
const queue = report.handoff_queue;
export const QUEUE = {
  scenarios: queue.scenarios,
  disputesPerDay: queue.disputes_per_day,
  peakSlot: slot(queue.peak_cell),
  peakPerWeek: queue.disputes_per_week_peak_cell,
  medianPerWeek: queue.disputes_per_week_median_cell,
  weeks: queue.weeks_in_window,
  centerPeakSlot: slot(queue.interactions_peak_cell),
  centerPeakMinutes: queue.contact_center_agent_minutes_peak_hour,
};

/* ---------- 5. thresholds ---------- */
export const THRESHOLDS = report.thresholds;

/* ---------- 6. satisfaction ---------- */
const sat = report.satisfaction;
const csatBy = (resolved: boolean) => sat.survey_csat_by_first_contact_resolution.find((r) => r.was_resolved === resolved);
const csatResolved = csatBy(true);
const csatUnresolved = csatBy(false);
const disputeSat = sat.dispute_resolution_satisfaction.find((r) => r.is_unrecognized_charge);
const otherSat = sat.dispute_resolution_satisfaction.find((r) => !r.is_unrecognized_charge);
if (!csatResolved || !csatUnresolved || !disputeSat || !otherSat) throw new Error("insights.json satisfaction is incomplete");
export const SATISFACTION = {
  scale: sat.survey_csat_scale,
  resolved: csatResolved,
  unresolved: csatUnresolved,
  byReason: sat.survey_csat_by_reason.map((r) => ({ ...r, label: reasonLabel(r.reason_category) })),
  dispute: disputeSat,
  other: otherSat,
};

export const ASSUMPTIONS = report.assumptions;

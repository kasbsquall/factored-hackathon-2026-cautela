/**
 * Data for /insights, read from the report copies in src/reports/insights (scripts/copy-insights.mjs copies them from
 * data_analytics/reports and ml/reports at build time). Every figure on the page comes from these files: this module
 * only selects fields and formats them; the one derived value is the complaints share, computed from its counts.
 */
import workflow from "@/reports/insights/workflow_comparison.json";
import disputeChannels from "@/reports/insights/dispute_channel_mix.json";
import interactionChannels from "@/reports/insights/interaction_channel_mix.json";
import fcr from "@/reports/insights/fcr_by_contact_reason.json";
import byHour from "@/reports/insights/demand_by_hour.json";
import byWeekday from "@/reports/insights/demand_by_weekday.json";
import daily from "@/reports/insights/demand_daily.json";
import breakdowns from "@/reports/insights/dispute_breakdowns.json";
import limits from "@/reports/insights/data_limits.json";
import evaluation from "@/reports/insights/evaluation.json";

export const intFmt = new Intl.NumberFormat("en-US");
export const int = (n: number) => intFmt.format(n);
/** 0.18327 -> "18.3%" */
export const pct = (share: number, digits = 1) => `${(share * 100).toFixed(digits)}%`;

export const PROVENANCE = {
  warehouse: workflow.provenance.warehouse,
  gold: workflow.provenance.latest_gold_run,
  isFixture: workflow.provenance.source_is_fixture,
};

/* ---------- complaint types ---------- */
export const complaintTypes = workflow.data.map((t) => ({
  type: t.complaint_type,
  isDispute: t.is_unrecognized_charge,
  rank: t.volume_rank,
  complaints: t.complaints,
  all: t.all_complaints,
  share: t.share_of_complaints,
  shareCi: t.share_ci95 as [number, number],
  slaBreached: t.sla_breached,
  slaRate: t.sla_breach_rate,
  slaCi: t.sla_breach_ci95 as [number, number],
  escalated: t.escalated,
  resolvedN: t.with_resolution_days,
  p50: t.resolution_days_p50,
  p90: t.resolution_days_p90,
}));

const dispute = complaintTypes.find((t) => t.isDispute);
if (!dispute) throw new Error("workflow_comparison.json has no unrecognized-charge row");
export const DISPUTE = dispute;
export const RUNNER_UP = complaintTypes.find((t) => t.rank === 2) ?? null;

/* ---------- channels ---------- */
export const disputeChannelMix = disputeChannels.data.map((c) => ({ label: c.channel, count: c.complaints, share: c.share }));
export const disputeChannelDenominator = disputeChannels.data.reduce((sum, c) => sum + c.complaints, 0);
export const interactionChannelMix = interactionChannels.data.map((c) => ({ label: c.channel, count: c.interactions, share: c.share }));
export const interactionDenominator = interactionChannels.data.reduce((sum, c) => sum + c.interactions, 0);

/* ---------- first-contact resolution ---------- */
export const fcrByReason = fcr.data
  .filter((r) => r.reason_category !== "(all reasons)")
  .map((r) => ({
    label: r.reason_category,
    rate: r.fcr_rate,
    ci: r.fcr_ci95 as [number, number],
    resolved: r.resolved_first_contact,
    denominator: r.fcr_denominator,
    meanSeconds: r.duration_mean_seconds,
  }));
export const fcrAll = fcr.data.find((r) => r.reason_category === "(all reasons)") ?? null;
const complaintFcr = fcrByReason.find((r) => r.label === "Complaint");
if (!complaintFcr) throw new Error("fcr_by_contact_reason.json has no Complaint row");
export const COMPLAINT_FCR = complaintFcr;

/* ---------- demand ---------- */
export const disputesByHour = byHour.data.disputes.map((b) => ({ bucket: b.bucket, count: b.contacts, share: b.share }));
export const disputesByWeekday = byWeekday.data.disputes.map((b) => ({ bucket: b.bucket, count: b.contacts, share: b.share }));
export const WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const dailyAll = daily.data.disputes[0];
if (!dailyAll) throw new Error("demand_daily.json has no disputes row");
export const DAILY = dailyAll;

/* ---------- country and segment ---------- */
type Group = (typeof breakdowns.data.country)[number];
const group = (g: Group) => ({
  label: g.value,
  complaints: g.complaints,
  customers: g.customers,
  per1000: g.disputes_per_1000_customers,
  slaRate: g.sla_breach_rate,
  slaBreached: g.sla_breached,
  p50: g.resolution_days_p50,
  p90: g.resolution_days_p90,
});
export const byCountry = breakdowns.data.country.map(group);
export const bySegment = breakdowns.data.segment.map(group);

/* ---------- data limits ---------- */
export const LIMITS = {
  typeVolume: limits.data.complaint_type_volume,
  slaRange: limits.data.sla_breach_rate_range as [number, number],
  descriptions: limits.data.dispute_descriptions,
  links: limits.data.complaint_links,
  transcripts: limits.data.transcripts,
  hourOfDay: limits.data.interaction_hour_of_day,
};

/* ---------- offline evaluation (ml/reports/results_fresh.json) ---------- */
export const SYSTEM_LABEL: Record<string, string> = {
  rules_fixed: "Rules, fixed thresholds",
  rules_tuned: "Rules, tuned thresholds (baseline)",
  learned_ranker_calibrated: "Learned ranker, calibrated thresholds",
  learned_ranker_disposition: "Learned ranker and disposition model (proposed)",
};
export const EVAL = evaluation;
const proposed = evaluation.systems.find((s) => s.id === evaluation.proposed_system);
const baseline = evaluation.systems.find((s) => s.id === evaluation.baseline_system);
if (!proposed || !baseline) throw new Error("evaluation.json lacks the proposed or baseline system");
export const PROPOSED = proposed;
export const BASELINE = baseline;

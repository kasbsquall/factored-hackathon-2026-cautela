/**
 * Copy the analytics and evaluation reports the /insights page charts into src/reports/insights, at build time.
 *
 * Sources (the repo's own committed reports, never fetched at runtime):
 *   ../data_analytics/reports/*.json   written by `make analytics` (data_analytics/run.py)
 *   ../ml/reports/results_fresh.json   written once by `ml.evaluate --split test_fresh`
 *   ../agent/policy/rules.yaml, ../agent/tools/ranking.py and ../ml/reports/fitted.json, for decision_rules.json:
 *     the thresholds and claim windows the "How Cautela decides" section shows, read from where the service reads them
 *
 * The analytics files are copied as they are. The evaluation file is large, so only the headline fields are kept
 * (per system: correct decisions, unsafe count, safe automated resolution, with their intervals and denominators).
 * When the sources are missing (a deploy that ships only app/), the committed copies are kept and the script says so.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { load as loadYaml } from "js-yaml";

const APP = join(dirname(fileURLToPath(import.meta.url)), "..");
const REPO = join(APP, "..");
const OUT = join(APP, "src", "reports", "insights");

const ANALYTICS = [
  "workflow_comparison",
  "dispute_channel_mix",
  "interaction_channel_mix",
  "fcr_by_contact_reason",
  "demand_by_hour",
  "demand_by_weekday",
  "demand_daily",
  "dispute_breakdowns",
  "data_limits",
  "insights",
];
const SYSTEMS = ["rules_fixed", "rules_tuned", "learned_ranker_calibrated", "learned_ranker_disposition"];

function write(name, value) {
  writeFileSync(join(OUT, `${name}.json`), `${JSON.stringify(value, null, 1)}\n`, "utf8");
}

function evaluation(source) {
  const r = JSON.parse(readFileSync(source, "utf8"));
  const systems = SYSTEMS.map((id) => {
    const s = r.systems[id].summary;
    return {
      id,
      decider: r.systems[id].decider,
      ranker: r.systems[id].ranker,
      correct_decision_rate: s.correct_decision_rate,
      safe_automated_resolution_rate: s.safe_automated_resolution_rate,
      unsafe: s.unsafe,
      decisions: s.decisions,
      automation_attempted_share: s.automation_attempted_share,
    };
  });
  return {
    source: "ml/reports/results_fresh.json",
    generated_by: r.generated_by,
    data_version: r.data_version,
    split: r.split,
    proposed_system: r.proposed_system,
    baseline_system: "rules_tuned",
    n_cases: r.systems[r.proposed_system].summary.n_cases,
    n_by_label: r.systems[r.proposed_system].summary.n_by_label,
    systems,
  };
}

/** A numeric constant from a Python module, e.g. `AMBIGUITY_MIN_TOP = 0.60`. */
function pyConstant(text, name) {
  const m = new RegExp(`^${name}[ \\t]*=[ \\t]*([0-9.]+)`, "m").exec(text);
  if (!m) throw new Error(`${name} not found`);
  return Number(m[1]);
}

/** The service's decision thresholds and claim windows, each with the file it comes from. */
function decisionRules(rulesPath, rankingPath, fittedPath) {
  const rules = loadYaml(readFileSync(rulesPath, "utf8"));
  const ranking = readFileSync(rankingPath, "utf8");
  const fitted = JSON.parse(readFileSync(fittedPath, "utf8"));
  const system = "learned_ranker_disposition";
  const policy = fitted.systems[system].policy;
  const pick = (r) => ({ id: r.id, source: r.source, verification: r.verification ?? null });
  return {
    sources: { policy: "agent/policy/rules.yaml", ranking: "agent/tools/ranking.py", fitted: "ml/reports/fitted.json" },
    policy_version: rules.version,
    claim_windows: rules.claim_windows.map((w) => ({
      ...pick(w), country: w.country, products: w.products, channels: w.channels, days: w.days, calendar: w.calendar,
      bank_response: w.bank_response ?? null,
    })),
    statuses: rules.disputable_status.map((r) => ({ ...pick(r), statuses: r.statuses })),
    amount_review: { ...pick(rules.amount_review), threshold_usd: rules.amount_review.threshold_usd },
    missing_data: pick(rules.missing_data),
    fx_rates: { ...pick(rules.fx_rates), units_per_usd: Object.fromEntries(Object.entries(rules.fx_rates.units_per_usd).map(([c, v]) => [c, v.rate])) },
    fraud: { ...pick(rules.fraud_escalation), score_at_least: rules.fraud_escalation.fraud_score_at_least, or_is_fraud: rules.fraud_escalation.or_is_fraud },
    confirmations: { ...pick(rules.confirmations), actions: rules.confirmations.actions },
    scope: pick(rules.out_of_scope),
    human: pick(rules.human_request),
    security: pick(rules.security_event),
    ambiguity: { min_top: pyConstant(ranking, "AMBIGUITY_MIN_TOP"), min_margin: pyConstant(ranking, "AMBIGUITY_MIN_MARGIN") },
    disposition: { system, t_act: policy.t_act, t_abstain: policy.t_abstain, act_floor: policy.act_floor, k: policy.k,
      max_unsafe_rate: policy.max_unsafe_rate, fitted_on: policy.fitted_on },
  };
}

mkdirSync(OUT, { recursive: true });
const missing = [];
for (const name of ANALYTICS) {
  const source = join(REPO, "data_analytics", "reports", `${name}.json`);
  if (existsSync(source)) write(name, JSON.parse(readFileSync(source, "utf8")));
  else missing.push(name);
}
const results = join(REPO, "ml", "reports", "results_fresh.json");
if (existsSync(results)) write("evaluation", evaluation(results));
else missing.push("evaluation");
const decisionSources = [join(REPO, "agent", "policy", "rules.yaml"), join(REPO, "agent", "tools", "ranking.py"), join(REPO, "ml", "reports", "fitted.json")];
if (decisionSources.every((f) => existsSync(f))) write("decision_rules", decisionRules(...decisionSources));
else missing.push("decision_rules");

if (!missing.length) {
  console.log(`copy-insights: ${ANALYTICS.length + 2} files written to src/reports/insights`);
} else {
  const absent = missing.filter((name) => !existsSync(join(OUT, `${name}.json`)));
  console.warn(`copy-insights: sources not found for ${missing.join(", ")}; keeping the committed copies`);
  if (absent.length) {
    console.error(`copy-insights: no committed copy of ${absent.join(", ")}; /insights cannot build`);
    process.exit(1);
  }
}

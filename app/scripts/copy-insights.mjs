/**
 * Copy the analytics and evaluation reports the /insights page charts into src/reports/insights, at build time.
 *
 * Sources (the repo's own committed reports, never fetched at runtime):
 *   ../data_analytics/reports/*.json   written by `make analytics` (data_analytics/run.py)
 *   ../ml/reports/results_fresh.json   written once by `ml.evaluate --split test_fresh`
 *
 * The analytics files are copied as they are. The evaluation file is large, so only the headline fields are kept
 * (per system: correct decisions, unsafe count, safe automated resolution, with their intervals and denominators).
 * When the sources are missing (a deploy that ships only app/), the committed copies are kept and the script says so.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

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

if (!missing.length) {
  console.log(`copy-insights: ${ANALYTICS.length + 1} files written to src/reports/insights`);
} else {
  const absent = missing.filter((name) => !existsSync(join(OUT, `${name}.json`)));
  console.warn(`copy-insights: sources not found for ${missing.join(", ")}; keeping the committed copies`);
  if (absent.length) {
    console.error(`copy-insights: no committed copy of ${absent.join(", ")}; /insights cannot build`);
    process.exit(1);
  }
}

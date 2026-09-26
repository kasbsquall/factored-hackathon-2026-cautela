import { ChatCircleDots, Eye, HandTap, Signpost, UserSwitch, type Icon } from "@phosphor-icons/react";
import { DECISION, type RuleSource } from "@/lib/insights";
import styles from "./decides.module.css";

const SOURCE_LABEL: Record<RuleSource, string> = { legal: "law", synthetic_policy: "synthetic policy" };
const VERIFIED: Record<string, string> = {
  verified_primary: "read in the primary legal text",
  team_research: "from team research, not re-read for this build",
  pending_verification: "not verified",
  measured_in_data: "measured on the dataset",
};

function Rule({ id, source }: { id: string; source: RuleSource }) {
  return (
    <span className={styles.rule}>
      <span className="mono">{id}</span>
      <span className={`${styles.src} ${source === "legal" ? styles.law : ""}`}>{SOURCE_LABEL[source]}</span>
    </span>
  );
}

/** Thresholds keep two decimals ("0.60"); a small fitted value keeps its own precision ("0.0464"). */
const num = (v: number) => (v >= 0.1 ? v.toFixed(2) : String(v));
const SPLIT: Record<string, string> = { val: "validation", train: "training" };
const days = (n: number, calendar: string) => `${n} ${calendar === "business" ? "business" : "calendar"} days`;
const list = (v: string | string[], all: string, lower = false) => {
  if (!Array.isArray(v)) return v === "any" ? all : v;
  const items = lower ? v.map((x) => `${x.toLowerCase()}s`) : v;
  return items.join(" and ");
};

interface Mode {
  icon: Icon;
  name: string;
  body: React.ReactNode;
  rules: { id: string; source: RuleSource }[];
}

/**
 * "How Cautela decides": the autonomy of each state and the thresholds that govern it, as the service runs them.
 * Every value comes from decision_rules.json, which scripts/copy-insights.mjs reads from agent/policy/rules.yaml,
 * agent/tools/ranking.py and ml/reports/fitted.json at build time.
 */
export function Decides() {
  const d = DECISION;
  const disp = d.disposition;
  const actAt = Math.max(disp.t_act, disp.act_floor);
  const rates = Object.entries(d.fx_rates.units_per_usd).map(([c, r]) => `${c} ${r.toLocaleString("en-US")}`).join(", ");
  const statusRule = (status: string) => d.statuses.find((s) => s.statuses.includes(status));
  const declined = statusRule("Declined");
  const reversed = statusRule("Reversed");
  const disputable = statusRule("Approved");

  const modes: Mode[] = [
    {
      icon: Eye,
      name: "Acts alone",
      body: <>Reads the customer&rsquo;s own charges, profile and the dispute policy with read-only tools. Explains a {declined?.statuses.join(" or ")} or {reversed?.statuses.join(" or ")} charge without opening anything, since no money is left to recover. It never writes on its own.</>,
      rules: [declined, reversed].filter((r): r is NonNullable<typeof r> => Boolean(r)).map((r) => ({ id: r.id, source: r.source })),
    },
    {
      icon: ChatCircleDots,
      name: "Asks the customer",
      body: <>Names one charge only when the model&rsquo;s match probability is at least <span className="num">{num(actAt)}</span>; otherwise it lists up to <span className="num">{disp.k}</span> candidates and the customer picks. Before opening a dispute it shows the charge as the tools read it and asks whether the customer recognizes it.</>,
      rules: [],
    },
    {
      icon: HandTap,
      name: "Confirms before writing",
      body: <>{d.confirmations.actions.join(" and ")} run only after an explicit yes bound to that action and its arguments. Only {disputable?.statuses.join(" or ")} charges can be disputed, inside the country&rsquo;s claim window.</>,
      rules: [d.confirmations, ...(disputable ? [disputable] : [])].map((r) => ({ id: r.id, source: r.source })),
    },
    {
      icon: UserSwitch,
      name: "Hands off to a person",
      body: <>At USD <span className="num">{d.amount_review.threshold_usd}</span> or more (the case is filed, then a person reviews it); at a fraud score of <span className="num">{d.fraud.score_at_least}</span> or more, or a fraud flag (it also offers to block the card); when the charge is outside its claim window; when the USD amount cannot be established; when nothing fits (no-match probability at least <span className="num">{num(disp.t_abstain)}</span>); when the customer asks for a person, the request is outside dispute intake, a security check fires or a tool fails.</>,
      rules: [d.amount_review, d.fraud, d.missing_data, d.human, d.scope, d.security].map((r) => ({ id: r.id, source: r.source })),
    },
  ];

  const thresholds: { what: string; value: string; governs: string; kind: string; file: string; rule?: { id: string; source: RuleSource } }[] = [
    { what: "Match probability to act", value: `≥ ${num(actAt)}`, governs: "Names one charge instead of asking", kind: `fitted on the ${SPLIT[disp.fitted_on] ?? disp.fitted_on} split, at most ${(disp.max_unsafe_rate * 100).toFixed(0)}% unsafe decisions`, file: d.sources.fitted },
    { what: "Act floor", value: `≥ ${num(disp.act_floor)}`, governs: "Lowest match probability that may ever act", kind: "business rule, set before any test result", file: d.sources.fitted },
    { what: "No-match probability to hand off", value: `≥ ${num(disp.t_abstain)}`, governs: "Hands off instead of asking", kind: `fitted on the ${SPLIT[disp.fitted_on] ?? disp.fitted_on} split`, file: d.sources.fitted },
    { what: "Options shown", value: `up to ${disp.k}`, governs: "Candidates listed when it asks", kind: "fixed", file: d.sources.fitted },
    { what: "Rule baseline, no trained model", value: `top < ${num(d.ambiguity.min_top)} or margin < ${num(d.ambiguity.min_margin)}`, governs: "Asks instead of acting", kind: "fixed rule", file: d.sources.ranking },
    { what: "Human review amount", value: `USD ${d.amount_review.threshold_usd}`, governs: "Filed, then reviewed by a person", kind: "synthetic policy", file: d.sources.policy, rule: d.amount_review },
    { what: "Fraud", value: `score ≥ ${d.fraud.score_at_least}${d.fraud.or_is_fraud ? " or fraud flag" : ""}`, governs: "Hands off, offers a card block", kind: "synthetic policy", file: d.sources.policy, rule: d.fraud },
    { what: "Exchange rates, per USD", value: rates, governs: "USD value of a charge with no USD amount", kind: `synthetic policy, ${VERIFIED[d.fx_rates.verification ?? ""] ?? "unverified"}`, file: d.sources.policy, rule: d.fx_rates },
  ];

  return (
    <section className={styles.section} id="decides" aria-labelledby="decides-title">
      <header className={styles.head}>
        <p className="eyebrow"><span className="mono">06</span></p>
        <h2 id="decides-title" className={styles.h2}><Signpost aria-hidden />How Cautela decides</h2>
        <p className={styles.lede}>
          What the assistant does on its own, when it asks, when it needs a yes and when a person takes over, with the thresholds the
          service runs (policy version <span className="mono">{d.policy_version}</span>). Rules marked law come from a legal text;
          synthetic policy is a team value the bank would replace with its own.
        </p>
      </header>

      <ol className={styles.modes}>
        {modes.map((m, i) => (
          <li key={m.name} className={`${styles.mode} rise-row`} style={{ "--i": Math.min(i, 7) } as React.CSSProperties}>
            <span className={styles.modeIcon} aria-hidden><m.icon /></span>
            <div className={styles.modeBody}>
              <h3 className={styles.modeName}><span className={`${styles.step} num`}>{i + 1}</span>{m.name}</h3>
              <p>{m.body}</p>
              {m.rules.length ? <p className={styles.rules} aria-label="Rules">{m.rules.map((r) => <Rule key={r.id} {...r} />)}</p> : null}
            </div>
          </li>
        ))}
      </ol>

      <p className={styles.narrow}>
        <strong>Policy can only narrow a model proposal.</strong> The model proposes, and the policy engine decides what is allowed: a
        proposal can remove an action or add an escalation, never add an action the rules did not allow, skip a confirmation or cite a
        reason the rules do not know (<span className="mono">agent/policy/engine.py</span>, <span className="mono">narrow</span>).
      </p>

      <div className={styles.tables}>
        <div className={styles.tableBlock}>
          <h3 className={styles.h3}>Thresholds</h3>
          <div className={styles.scroll} role="region" aria-label="Thresholds" tabIndex={0}>
            <table className={`${styles.table} num`}>
              <thead><tr><th scope="col">Threshold</th><th scope="col">Value</th><th scope="col">Governs</th><th scope="col">Kind</th><th scope="col">Source</th></tr></thead>
              <tbody>
                {thresholds.map((t) => (
                  <tr key={t.what}>
                    <th scope="row">{t.what}</th>
                    <td className={styles.value}>{t.value}</td>
                    <td>{t.governs}</td>
                    <td>{t.kind}</td>
                    <td>{t.rule ? <Rule {...t.rule} /> : null}<span className={`${styles.file} mono`}>{t.file}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className={styles.tableBlock}>
          <h3 className={styles.h3}>Claim windows</h3>
          <div className={styles.scroll} role="region" aria-label="Claim windows" tabIndex={0}>
            <table className={`${styles.table} num`}>
              <thead><tr><th scope="col">Rule</th><th scope="col">Country</th><th scope="col">Applies to</th><th scope="col">Customer has</th><th scope="col">Bank answers within</th><th scope="col">Verification</th></tr></thead>
              <tbody>
                {d.claim_windows.map((w) => (
                  <tr key={w.id}>
                    <th scope="row"><Rule id={w.id} source={w.source} /></th>
                    <td>{w.country}</td>
                    <td>{list(w.products, "any product", true)}, {list(w.channels, "any channel")}</td>
                    <td className={styles.value}>{days(w.days, w.calendar)}</td>
                    <td>
                      {w.bank_response ? days(w.bank_response.days, w.bank_response.calendar) : "not set"}
                      {w.bank_response?.foreign_days ? ` (${w.bank_response.foreign_days} for a charge made abroad)` : ""}
                    </td>
                    <td>{VERIFIED[w.verification ?? ""] ?? "not stated"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className={styles.foot}>
            Where two rules cover a country, the more specific one applies (Colombia&rsquo;s law covers Web and App; Argentina&rsquo;s covers
            credit cards) and the other covers the rest. Windows are counted from the transaction date, because the dataset has no statement dates; that can only shorten
            a window, never lengthen it. Sources: <span className="mono">{d.sources.policy}</span>, <span className="mono">{d.sources.ranking}</span>, <span className="mono">{d.sources.fitted}</span>.
          </p>
        </div>
      </div>
    </section>
  );
}

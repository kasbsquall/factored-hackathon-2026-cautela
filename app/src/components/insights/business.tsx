import { CaretRight, Info, Table } from "@phosphor-icons/react";
import {
  ASSUMPTIONS, BUSINESS_SOURCE, COST, PROJECTION, QUEUE, SATISFACTION, THRESHOLDS, dec, reachLabel, usd,
} from "@/lib/insights-business";
import { int, pct } from "@/lib/insights";
import { Assumption, Figure, Ledger, Source } from "./business-parts";
import styles from "./business.module.css";

/** Four significant digits for token costs: 0.0005812 (the unit, USD, goes under the figure). */
const tiny = (value: number) => new Intl.NumberFormat("en-US", { maximumSignificantDigits: 4 }).format(value);
/** The report's rate keys, named as ml/ and eval/ name them. */
const RATE_NAME: Record<string, string> = {
  conservative: "end-to-end safe automated resolution, deployed",
  optimistic: "safe act rate (component)",
};
const HANDLING_NAME: Record<string, string> = { low: "low", central: "central", high: "high" };

function monthName(month: string): string {
  const [y, m] = month.split("-").map(Number);
  if (!y || !m) return month;
  return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" }).format(Date.UTC(y, m - 1, 1));
}

type Headline = typeof PROJECTION.conservative;

function End({ title, h, assumption }: { title: string; h: Headline; assumption?: boolean }) {
  const reach = reachLabel(h.reach);
  return (
    <div className={styles.end}>
      <p className={styles.endLabel}>{title}{assumption ? <Assumption /> : null}</p>
      <p className={styles.endValue}>{dec(h.hours_saved_per_year)}</p>
      <p className={styles.endUnit}>agent-hours saved per year</p>
      <p className={`${styles.endMoney} num`}>{usd(h.net_savings_per_year_usd)} net per year</p>
      <dl className={styles.endFacts}>
        <div><dt>Reach</dt><dd>{pct(h.reach_share)} of disputes ({reach.name})</dd></div>
        <div><dt>Automation rate</dt><dd>{pct(h.safe_automated_resolution_rate)}, {RATE_NAME[h.rate] ?? h.rate}</dd></div>
        <div><dt>Share of contact-center hours</dt><dd>{pct(h.share_of_contact_center_hours, 2)}</dd></div>
        <div><dt>Handoffs to a person</dt><dd>{int(Math.round(h.handoffs_per_year))} per year</dd></div>
      </dl>
    </div>
  );
}

/** The savings range (the hero of the business part), its full grid, and the cost of one resolution. */
export function Savings() {
  const p = PROJECTION;
  const top = Math.max(...p.rows.map((r) => r.hours_saved_per_year));
  const from = p.conservative.hours_saved_per_year / top;
  const to = p.optimistic.hours_saved_per_year / top;
  return (
    <div className={styles.block}>
      <div className={`${styles.range} rise`}>
        <div className={styles.ends}>
          <End title="Conservative, chat only" h={p.conservative} />
          <span className={styles.to} aria-hidden>to</span>
          <End title="Optimistic, with the call center" h={p.optimistic} assumption />
        </div>
        <div>
          <div className={styles.track} role="img"
            aria-label={`Range from ${dec(p.conservative.hours_saved_per_year)} to ${dec(p.optimistic.hours_saved_per_year)} agent-hours per year, on a scale up to ${dec(top)}, the highest scenario in the grid`}>
            <span className={styles.span} style={{ "--from": `${from * 100}%`, "--width": `${(to - from) * 100}%` } as React.CSSProperties} />
          </div>
          <p className={`${styles.axis} num`}><span>0 h</span><span>{dec(top)} h per year, highest scenario in the grid</span></p>
        </div>
        <p className={styles.caveat}>
          Central handling time ({dec(COST.humanMinutes, 2)} min per dispute), {usd(p.hourlyCost)} per agent-hour as a placeholder. A conversation the
          bot does not resolve safely is charged its tokens and then the full human handling time. Offline projection: no saving was measured in
          production.
        </p>
        <details className={styles.grid}>
          <summary><Table aria-hidden /><span>Full grid: {p.rows.length} scenarios of handling time, reach and rate</span><CaretRight aria-hidden className={styles.caret} /></summary>
          <div className={styles.gridBody}>
            <Ledger label="Savings grid" columns={9}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th scope="col">Handling time</th><th scope="col">Reach</th><th scope="col">Rate</th>
                    <th scope="col" className={styles.n}>Disputes sent to the bot per year</th>
                    <th scope="col" className={styles.n}>Agent-hours saved per year</th>
                    <th scope="col" className={styles.n}>Agent-hours still needed per year</th>
                    <th scope="col" className={styles.n}>Share of contact-center hours</th>
                    <th scope="col" className={styles.n}>LLM cost per year</th>
                    <th scope="col" className={styles.n}>Net saved per year</th>
                  </tr>
                </thead>
                <tbody>
                  {p.rows.map((r) => {
                    const reach = reachLabel(r.reach);
                    return (
                      <tr key={`${r.handling}-${r.reach}-${r.rate}`}>
                        <th scope="row">{HANDLING_NAME[r.handling] ?? r.handling}</th>
                        <td>{reach.name} ({pct(r.reach_share)}){reach.assumption ? <> <Assumption /></> : null}</td>
                        <td>{RATE_NAME[r.rate] ?? r.rate} ({pct(r.safe_automated_resolution_rate)})</td>
                        <td className={styles.n}>{dec(r.disputes_routed_per_year)}</td>
                        <td className={`${styles.n} ${styles.key}`}>{dec(r.hours_saved_per_year)} h</td>
                        <td className={styles.n}>{dec(r.human_hours_left_per_year)} h</td>
                        <td className={styles.n}>{pct(r.share_of_contact_center_hours, 2)}</td>
                        <td className={styles.n}>{usd(r.llm_usd_per_year, 2)}</td>
                        <td className={styles.n}>{usd(r.net_savings_per_year_usd)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Ledger>
          </div>
        </details>
      </div>
      <Source>{BUSINESS_SOURCE}, projection_range</Source>
    </div>
  );
}

export function CostPerResolution() {
  const c = COST;
  return (
    <div className={styles.block}>
      <h3 className={styles.h3}>Cost of one resolution</h3>
      <dl className={styles.figures}>
        <Figure label="Human handling per dispute" value={`${dec(c.humanMinutes, 2)} min`} unit="complaint-contact mean, one contact" />
        <Figure label="LLM cost per safe automated resolution" value={tiny(c.llmPerSafe)} unit="USD, tokens only, measured" tone="lead" />
        <Figure label="LLM cost per conversation" value={tiny(c.llmPerConversation)} unit="USD, tokens only, measured" />
        <Figure label="Hosting and compute" value="not defined" unit="not measured, excluded from every figure" tone="muted" />
      </dl>
      <Ledger label="Cost per dispute by hourly cost" columns={3}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">Agent cost (placeholder)</th>
              <th scope="col" className={styles.n}>Human cost per dispute</th>
              <th scope="col" className={styles.n}>Cost per dispute sent to the bot, {c.rate} rate</th>
            </tr>
          </thead>
          <tbody>
            {c.human.map((h) => {
              const bot = c.withBot.find((b) => b.hourly === h.hourly);
              return (
                <tr key={h.hourly}>
                  <th scope="row" className="num">{usd(h.hourly)} per agent-hour</th>
                  <td className={styles.n}>{usd(h.value, 2)}</td>
                  <td className={`${styles.n} ${styles.key}`}>{bot ? usd(bot.value, 4) : "not reported"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Ledger>
      <p className={styles.sub}>The bot column adds the tokens of every conversation and the full human cost of each one it does not resolve safely.</p>
      <Source>{BUSINESS_SOURCE}, cost_per_resolution</Source>
    </div>
  );
}

export function HandoffQueue() {
  const q = QUEUE;
  const heaviest = q.scenarios.reduce((a, b) => (b.agent_minutes_peak_hour > a.agent_minutes_peak_hour ? b : a));
  return (
    <div className={styles.block}>
      <dl className={styles.figures}>
        <Figure label="Disputes per day, all channels" value={`${dec(q.disputesPerDay.mean_per_day)}`} unit={`mean; p95 ${dec(q.disputesPerDay.p95_per_day)}, max ${q.disputesPerDay.max_per_day} per day`} />
        <Figure label="Agent-minutes in the busiest hour, heaviest scenario" value={`${dec(heaviest.agent_minutes_peak_hour)} min`} unit={`${q.peakSlot}, disputes as stored`} tone="lead" />
        <Figure label="Agent-minutes the contact center already handles in its busiest hour" value={`${dec(q.centerPeakMinutes)} min`} unit={`${q.centerPeakSlot}, all contact reasons`} />
      </dl>
      <Ledger label="Handoffs to a person by scenario" columns={8}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">Reach</th><th scope="col">Rate</th>
              <th scope="col" className={styles.n}>Handoffs, share of disputes</th>
              <th scope="col" className={styles.n}>Handoffs per day, mean</th>
              <th scope="col" className={styles.n}>Handoffs per day, p95</th>
              <th scope="col" className={styles.n}>Handoffs in the busiest hour</th>
              <th scope="col" className={styles.n}>Agent-minutes in the busiest hour</th>
              <th scope="col" className={styles.n}>Agent-minutes on a p95 day</th>
            </tr>
          </thead>
          <tbody>
            {q.scenarios.map((s, i) => {
              const reach = reachLabel(s.reach);
              return (
                <tr key={`${s.reach}-${s.rate}`} className="rise-row" style={{ "--i": Math.min(i, 7) } as React.CSSProperties}>
                  <th scope="row">{reach.name}{reach.assumption ? <> <Assumption /></> : null}</th>
                  <td>{RATE_NAME[s.rate] ?? s.rate}</td>
                  <td className={styles.n}>{pct(s.handoff_share_of_disputes)}</td>
                  <td className={`${styles.n} ${styles.key}`}>{dec(s.handoffs_per_day_mean)}</td>
                  <td className={styles.n}>{dec(s.handoffs_per_day_p95)}</td>
                  <td className={styles.n}>{dec(s.handoffs_peak_hour, 2)}</td>
                  <td className={styles.n}>{dec(s.agent_minutes_peak_hour, 2)} min</td>
                  <td className={styles.n}>{dec(s.agent_minutes_p95_day)} min</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Ledger>
      <p className={styles.sub}>
        Handoffs = disputes &times; reach &times; (1 &minus; safe automated resolution), each at {dec(COST.humanMinutes, 2)} agent-minutes. The busiest
        hour is the weekday-hour cell with the most disputes ({q.peakSlot}, {dec(q.peakPerWeek, 2)} disputes a week against {dec(q.medianPerWeek, 2)} in
        the median cell, over {dec(q.weeks)} weeks). Hours are as stored; the data gives no timezone.
      </p>
      <Source>{BUSINESS_SOURCE}, handoff_queue</Source>
    </div>
  );
}

export function Thresholds() {
  const { amount, fraud } = THRESHOLDS;
  const pcts = Object.entries(amount.percentiles_usd) as [string, number][];
  return (
    <div className={styles.pair}>
      <div className={styles.block}>
        <h3 className={styles.h3}><span className="mono">{amount.rule_id}</span> USD {int(amount.threshold_usd)} or more</h3>
        <dl className={styles.figures}>
          <Figure label="Disputable charges sent to a person" value={pct(amount.share_routed)} unit={`of ${int(amount.disputable_charges)} charges`} tone="lead" />
          <Figure label={`Purchase p90, ${monthName(amount.eda_month.month)}`} value={usd(amount.eda_month.purchase_p90_usd, 2)}
            unit={`${int(amount.eda_month.purchases_with_usd)} purchases with a USD amount`} />
          <Figure label={`Monthly purchase p90, ${amount.purchase_p90_by_month.months} months`} value={`${usd(amount.purchase_p90_by_month.min, 2)}`}
            unit={`to ${usd(amount.purchase_p90_by_month.max, 2)}`} />
        </dl>
        <dl className={styles.pcts} aria-label="USD amount of disputable charges, percentiles">
          {pcts.map(([k, v]) => (
            <div key={k} className={v >= amount.threshold_usd && pcts.find(([, x]) => x >= amount.threshold_usd)?.[0] === k ? styles.hit : undefined}>
              <dt>{k}</dt><dd>{usd(v, 2)}</dd>
            </div>
          ))}
        </dl>
        <p className={styles.sub}>
          USD amount of every disputable charge in the window, by percentile; the first percentile at or above the threshold is marked. The threshold
          sits at the purchase p90 of the month the rule cites, and every month&rsquo;s purchase p90 is close to it. Transfers and payments are larger,
          so they carry most of the {pct(amount.share_routed)}.
        </p>
      </div>
      <div className={styles.block}>
        <h3 className={styles.h3}><span className="mono">{fraud.rule_id}</span> Fraud score {int(fraud.score_at_least)} or more, or the fraud flag</h3>
        <dl className={styles.figures}>
          <Figure label="Disputable charges sent to a person" value={pct(fraud.share_routed, 3)} unit="by the fraud rule" tone="lead" />
          <Figure label="Highest score of an unflagged charge" value={dec(fraud.unflagged_max_score)} unit={`threshold ${int(fraud.score_at_least)}`} />
          <Figure label="Sent by the score alone" value={int(fraud.routed_by_score_only)} unit="charges without the flag" />
        </dl>
        <p className={styles.plain}>
          <Info aria-hidden />
          <span>
            In this data the score threshold adds nothing beyond the flag. No unflagged charge scores above {dec(fraud.unflagged_max_score)}, so every
            charge the rule sends to a person is already flagged. Together the two rules send {pct(THRESHOLDS.either_share_routed)} of disputable
            charges to a person.
          </span>
        </p>
      </div>
      <Source>{BUSINESS_SOURCE}, thresholds (rules version {THRESHOLDS.policy_version})</Source>
    </div>
  );
}

export function Satisfaction() {
  const s = SATISFACTION;
  const gap = s.resolved.mean_score - s.unresolved.mean_score;
  return (
    <div className={styles.block}>
      <dl className={styles.figures}>
        <Figure label="CSAT, resolved on the first contact" value={dec(s.resolved.mean_score, 2)} unit={`of ${s.scale.max_score}; ${int(s.resolved.surveys)} surveys`} tone="lead" />
        <Figure label="CSAT, not resolved on the first contact" value={dec(s.unresolved.mean_score, 2)} unit={`of ${s.scale.max_score}; ${int(s.unresolved.surveys)} surveys`} />
        <Figure label="Difference" value={`${dec(gap, 2)} points`} unit={`on a ${s.scale.min_score} to ${s.scale.max_score} scale`} />
      </dl>
      <Ledger label="CSAT by contact reason" columns={6}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">Contact reason</th>
              <th scope="col" className={styles.n}>Surveys</th>
              <th scope="col" className={styles.n}>Mean CSAT</th>
              <th scope="col" className={styles.n}>Mean CSAT, resolved first time</th>
              <th scope="col" className={styles.n}>Mean CSAT, not resolved</th>
              <th scope="col" className={styles.n}>First-contact resolution of the surveyed</th>
            </tr>
          </thead>
          <tbody>
            {s.byReason.map((r, i) => (
              <tr key={r.reason_category} className={`${r.label === "Complaint" ? styles.chosen : ""} rise-row`} style={{ "--i": Math.min(i, 7) } as React.CSSProperties}>
                <th scope="row">{r.label}</th>
                <td className={styles.n}>{int(r.surveys)}</td>
                <td className={`${styles.n} ${styles.key}`}>{dec(r.mean_score, 2)} of {s.scale.max_score}</td>
                <td className={styles.n}>{dec(r.mean_resolved, 2)} of {s.scale.max_score}</td>
                <td className={styles.n}>{dec(r.mean_unresolved, 2)} of {s.scale.max_score}</td>
                <td className={styles.n}>{pct(r.fcr_of_surveyed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Ledger>
      <p className={styles.sub}>
        Mean CSAT by reason follows its first-contact resolution; Complaint, where disputes are filed, is lowest. Surveys link to contact-center
        interactions and never to complaints, so no survey can be tied to a dispute.
      </p>
      <p className={styles.plain}>
        <Info aria-hidden />
        <span>
          <strong>Dispute resolution satisfaction has a small sample.</strong> The complaint-level score ({s.dispute.min_score} to {s.dispute.max_score})
          exists for {int(s.dispute.with_score)} of {int(s.dispute.complaints)} disputes ({pct(s.dispute.with_score / s.dispute.complaints)}) and
          averages {dec(s.dispute.mean_score, 2)}, against {dec(s.other.mean_score, 2)} for other complaints ({int(s.other.with_score)} of{" "}
          {int(s.other.complaints)}). It cannot measure a dispute outcome.
        </span>
      </p>
      <Source>{BUSINESS_SOURCE}, satisfaction</Source>
    </div>
  );
}

export function Assumptions() {
  return (
    <section className={styles.notes} aria-labelledby="assumptions">
      <h3 id="assumptions" className={styles.h3}>Assumptions behind the business figures</h3>
      <ol>
        {ASSUMPTIONS.map((a) => <li key={a}>{a.charAt(0).toUpperCase() + a.slice(1)}</li>)}
      </ol>
    </section>
  );
}

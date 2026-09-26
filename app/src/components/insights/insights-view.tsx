"use client";

import {
  CalendarBlank, ChartBar, ClockCountdown, Flask, Headset, Info, ListNumbers, MapTrifold, Scales, ShieldWarning, Timer,
  UsersThree, type Icon,
} from "@phosphor-icons/react";
import {
  BASELINE, COMPLAINT_FCR, DAILY, DISPUTE, EVAL, LIMITS, PROPOSED, PROVENANCE, RUNNER_UP, SYSTEM_LABEL, WEEKDAY, byCountry,
  bySegment, complaintTypes, disputeChannelDenominator, disputeChannelMix, disputesByHour, disputesByWeekday, fcrAll,
  fcrByReason, int, pct,
} from "@/lib/insights";
import { BarList, ColumnChart, DotRange } from "./charts";
import { Decides } from "./decides";
import styles from "./insights.module.css";

/** English for the complaint types' Spanish subcategories, as the organizer data names them. */
const GLOSS: Record<string, string> = {
  "Cargo no reconocido": "unrecognized charge",
  "Cobro indebido": "wrongful charge or fee",
  "Problema con app": "app problem",
  "Atención en sucursal": "branch service",
  "Calidad de servicio": "service quality",
  "(no subcategory)": "no subcategory",
};

function typeLabel(type: string): string {
  const [area, sub] = type.split(" / ");
  if (!sub) return type;
  return sub === "(no subcategory)" ? `${area}, no subcategory` : `${area}: ${GLOSS[sub] ?? sub} (${sub})`;
}

const SRC = "data_analytics/reports";

/** "pair" sets two charts side by side on wide screens; "stack" gives long-label charts the full width. */
function Section({ n, icon: SectionIcon, title, lede, layout = "pair", children }: { n: number; icon: Icon; title: string; lede: string; layout?: "pair" | "stack"; children: React.ReactNode }) {
  return (
    <section className={styles.section} aria-labelledby={`s${n}`}>
      <header className={styles.sectionHead}>
        <p className="eyebrow"><span className="mono">{String(n).padStart(2, "0")}</span></p>
        <h2 id={`s${n}`} className={styles.h2}><SectionIcon aria-hidden />{title}</h2>
        <p className={styles.lede}>{lede}</p>
      </header>
      <div className={`${styles.charts} ${layout === "stack" ? styles.stack : ""}`}>{children}</div>
    </section>
  );
}

function Stat({ icon: StatIcon, label, value, unit, note }: { icon: Icon; label: string; value: string; unit: string; note: string }) {
  return (
    <div className={styles.stat}>
      <dt><StatIcon aria-hidden />{label}</dt>
      <dd>
        <span className={styles.statValue}>{value}</span>
        <span className={styles.statUnit}>{unit}</span>
      </dd>
      <dd className={styles.statNote}>{note}</dd>
    </div>
  );
}

export function InsightsView() {
  const d = DISPUTE;
  const lead = RUNNER_UP ? d.complaints - RUNNER_UP.complaints : null;
  const overlap = RUNNER_UP ? d.shareCi[0] <= RUNNER_UP.shareCi[1] : false;
  const busiestHour = disputesByHour.reduce((a, b) => (b.count > a.count ? b : a));
  const quietestHour = disputesByHour.reduce((a, b) => (b.count < a.count ? b : a));
  const busiestDay = disputesByWeekday.reduce((a, b) => (b.count > a.count ? b : a));
  const quietestDay = disputesByWeekday.reduce((a, b) => (b.count < a.count ? b : a));
  const topChannel = disputeChannelMix.reduce((a, b) => (b.count > a.count ? b : a));
  const systems = EVAL.systems;
  const hourLabel = (h: number) => `${String(h).padStart(2, "0")}:00`;

  return (
    <div className={styles.page}>
      <header className={`${styles.hero} rise`}>
        <p className={styles.kicker}><ChartBar aria-hidden />Data analytics · workflow choice</p>
        <h1 className={styles.h1}>Why Cautela starts with unrecognized-charge disputes</h1>
        <div className={styles.headline}>
          <p className={styles.big}>
            <span className={styles.bigValue}>{int(d.complaints)}</span>
            <span className={styles.bigOf}>of {int(d.all)} complaints</span>
          </p>
          <p className={styles.bigText}>
            are &ldquo;cargo no reconocido&rdquo;, a charge the customer does not recognize (called disputes on this page): {pct(d.share)} of all complaints, the
            largest of {complaintTypes.length} complaint types
            {lead !== null && RUNNER_UP ? `, ahead of the next one by only ${int(lead)} complaints${overlap ? " (their 95% intervals overlap)" : ""}` : ""}.
          </p>
        </div>
        <p className={styles.provenance}>
          <Flask aria-hidden />
          <span>
            Organizer dataset, which is synthetic: nothing here describes a real bank. Warehouse <span className="mono">{PROVENANCE.warehouse}</span>,
            gold run <span className="mono">{PROVENANCE.gold}</span>.
          </span>
        </p>
      </header>

      <dl className={`${styles.stats} rise`} style={{ "--i": 1 } as React.CSSProperties}>
        <Stat icon={ClockCountdown} label="SLA breached" value={pct(d.slaRate)} unit="of disputes" note={`${int(d.slaBreached)} of ${int(d.complaints)} disputes`} />
        <Stat icon={Timer} label="Resolution time" value={`${d.p50}`} unit={`days median, ${d.p90} at p90`} note={`measured on the ${int(d.resolvedN)} disputes with a resolution time`} />
        <Stat icon={CalendarBlank} label="Daily demand" value={`${DAILY.mean_per_day}`} unit="disputes per day" note={`p95 ${DAILY.p95_per_day} per day, max ${DAILY.max_per_day} per day, over ${int(DAILY.days)} days`} />
        <Stat icon={Headset} label="Largest intake channel" value={pct(topChannel.share)} unit={topChannel.label} note={`${int(topChannel.count)} of ${int(disputeChannelDenominator)} disputes`} />
      </dl>

      <Section n={1} icon={ListNumbers} layout="stack" title="The largest complaint type, by a thin margin"
        lede="Five complaint types sit within a few hundred complaints of each other. The volume lead alone would not justify the choice; the checks below and the fact that a disputed charge can be verified against the customer's own transactions do.">
        <BarList index={0} title="Complaints by type" sub={`Count of complaints per type, out of ${int(d.all)}. Highlighted bar: unrecognized charge.`}
          source={`${SRC}/workflow_comparison.json`} hint="Hover, tap or focus a bar to read its share and interval. Every value is also in the table."
          rows={complaintTypes.map((t) => ({
            key: t.type, label: typeLabel(t.type), value: t.complaints, valueText: int(t.complaints), highlight: t.isDispute,
            detail: `${typeLabel(t.type)}: ${int(t.complaints)} of ${int(t.all)} complaints, ${pct(t.share)} (95% CI ${pct(t.shareCi[0])} to ${pct(t.shareCi[1])})`,
          }))}
          max={14000} ticks={[0, 5000, 10000]} tickText={int}
          table={{ head: ["Complaint type", "Complaints", "Share", "95% CI"], rows: complaintTypes.map((t) => [typeLabel(t.type), int(t.complaints), pct(t.share), `${pct(t.shareCi[0])} to ${pct(t.shareCi[1])}`]) }} />
        <DotRange index={1} title="SLA breached, by complaint type" sub={`Share of each type's complaints whose SLA was breached, with its 95% interval. Axis from 15% to 25%. Every type falls between ${pct(LIMITS.slaRange[0])} and ${pct(LIMITS.slaRange[1])}, so this metric cannot rank workflows.`}
          source={`${SRC}/workflow_comparison.json`} hint="Hover, tap or focus a row for the count behind the rate."
          rows={complaintTypes.map((t) => ({
            key: t.type, label: typeLabel(t.type), value: t.slaRate, ci: t.slaCi, valueText: pct(t.slaRate), highlight: t.isDispute,
            detail: `${typeLabel(t.type)}: ${int(t.slaBreached)} of ${int(t.complaints)} breached, ${pct(t.slaRate)} (95% CI ${pct(t.slaCi[0])} to ${pct(t.slaCi[1])})`,
          }))}
          domain={[0.15, 0.25]} ticks={[0.15, 0.2, 0.25]} tickText={(v) => pct(v, 0)}
          table={{ head: ["Complaint type", "SLA breached", "Rate", "95% CI", "Resolution p50 / p90 (days)"], rows: complaintTypes.map((t) => [typeLabel(t.type), `${int(t.slaBreached)} / ${int(t.complaints)}`, pct(t.slaRate), `${pct(t.slaCi[0])} to ${pct(t.slaCi[1])}`, `${t.p50} / ${t.p90}`]) }} />
      </Section>

      <Section n={2} icon={Headset} title="Where disputes arrive, and how complaint contacts end"
        lede="Half of the disputes come in by phone. In the contact center, complaint contacts have the lowest first-contact resolution of any reason.">
        <BarList index={0} title="Disputes by reception channel" sub={`Share of ${int(disputeChannelDenominator)} unrecognized-charge complaints, by the channel that received them.`}
          source={`${SRC}/dispute_channel_mix.json`} hint="Hover, tap or focus a bar for its count."
          rows={disputeChannelMix.map((c) => ({
            key: c.label, label: c.label, value: c.share, valueText: pct(c.share), highlight: c.label === topChannel.label,
            detail: `${c.label}: ${int(c.count)} of ${int(disputeChannelDenominator)} complaints, ${pct(c.share)}`,
          }))}
          max={0.6} ticks={[0, 0.2, 0.4, 0.6]} tickText={(v) => pct(v, 0)}
          table={{ head: ["Channel", "Complaints", "Share"], rows: disputeChannelMix.map((c) => [c.label, int(c.count), pct(c.share)]) }} />
        <div className={styles.withNote}>
          <BarList index={1} title="First-contact resolution, by contact reason" sub={`Share of contact-center interactions resolved on the first contact, with its 95% interval. All reasons together: ${fcrAll ? pct(fcrAll.fcr_rate) : "not reported"}.`}
            source={`${SRC}/fcr_by_contact_reason.json`} hint="Hover, tap or focus a bar for its count and mean handling time."
            rows={fcrByReason.map((r) => ({
              key: r.label, label: r.label, value: r.rate, ci: r.ci, valueText: pct(r.rate), highlight: r.label === "Complaint",
              detail: `${r.label}: ${int(r.resolved)} of ${int(r.denominator)} interactions resolved first time, ${pct(r.rate)}; mean handling ${Math.round(r.meanSeconds)} s`,
            }))}
            max={1} ticks={[0, 0.25, 0.5, 0.75, 1]} tickText={(v) => pct(v, 0)}
            table={{ head: ["Contact reason", "Resolved first time", "Rate", "95% CI", "Mean handling (s)"], rows: fcrByReason.map((r) => [r.label, `${int(r.resolved)} / ${int(r.denominator)}`, pct(r.rate), `${pct(r.ci[0])} to ${pct(r.ci[1])}`, `${Math.round(r.meanSeconds)}`]) }} />
          <p className={styles.note}>
            <Info aria-hidden />
            <span>
              <strong>Read with care.</strong> {pct(COMPLAINT_FCR.rate)} is the contact center&rsquo;s first-contact resolution for the
              Complaint reason ({int(COMPLAINT_FCR.resolved)} / {int(COMPLAINT_FCR.denominator)}). It cannot be linked to disputes:
              {" "}{int(LIMITS.links.with_origin_interaction)} of {int(LIMITS.links.complaints)} complaints name the interaction they came
              from, and which reason a disputed-charge call is filed under is not recorded. Filed as Transactional, its rate would be
              {" "}{pct(fcrByReason.find((r) => r.label === "Transactional")?.rate ?? 0)}.
            </span>
          </p>
        </div>
      </Section>

      <Section n={3} icon={CalendarBlank} title="When disputes arrive"
        lede="Tuesday to Friday carry the load, Monday is lower and weekends drop. Across the hours of the day the profile is nearly flat.">
        <ColumnChart index={0} title="Disputes by weekday" sub={`Unrecognized-charge complaints per ISO weekday, out of ${int(d.complaints)}.`}
          source={`${SRC}/demand_by_weekday.json`} hint="Hover, tap or focus a column for its count and share."
          rows={disputesByWeekday.map((b) => ({
            key: String(b.bucket), label: WEEKDAY[b.bucket - 1] ?? String(b.bucket), value: b.count,
            capText: b.bucket === busiestDay.bucket || b.bucket === quietestDay.bucket ? int(b.count) : undefined,
            detail: `${WEEKDAY[b.bucket - 1]}: ${int(b.count)} complaints, ${pct(b.share)} of ${int(d.complaints)}`,
          }))}
          max={2500} ticks={[0, 1000, 2000]} tickText={int}
          table={{ head: ["Weekday", "Complaints", "Share"], rows: disputesByWeekday.map((b) => [WEEKDAY[b.bucket - 1] ?? "", int(b.count), pct(b.share)]) }} />
        <ColumnChart index={1} title="Disputes by hour of day" sub={`Complaints per hour as stored, out of ${int(d.complaints)}. The data dictionary gives no timezone, so the hours may be shifted.`}
          source={`${SRC}/demand_by_hour.json`} hint="Hover, tap or focus a column for its count and share."
          rows={disputesByHour.map((b) => ({
            key: String(b.bucket), label: String(b.bucket).padStart(2, "0"), value: b.count, showLabel: b.bucket % 6 === 0,
            capText: b.bucket === busiestHour.bucket || b.bucket === quietestHour.bucket ? int(b.count) : undefined,
            detail: `${hourLabel(b.bucket)}: ${int(b.count)} complaints, ${pct(b.share)} of ${int(d.complaints)}`,
          }))}
          max={700} ticks={[0, 200, 400, 600]} tickText={int}
          table={{ head: ["Hour", "Complaints", "Share"], rows: disputesByHour.map((b) => [hourLabel(b.bucket), int(b.count), pct(b.share)]) }} />
      </Section>

      <Section n={4} icon={MapTrifold} title="By country and segment"
        lede="Every country and segment files about 81 to 83 disputes per 1,000 customers. The need is the same everywhere, so one workflow can serve all three countries, each with its own claim-window rules: law where the team verified one, synthetic policy elsewhere.">
        <BarList index={0} title="Disputes per 1,000 customers, by country" sub="Unrecognized-charge complaints divided by the country's customers, times 1,000."
          source={`${SRC}/dispute_breakdowns.json`} hint="Hover, tap or focus a bar for its counts."
          rows={byCountry.map((g) => ({
            key: g.label, label: g.label, value: g.per1000, valueText: `${g.per1000} per 1,000`,
            detail: `${g.label}: ${int(g.complaints)} complaints over ${int(g.customers)} customers, ${g.per1000} per 1,000; SLA breached ${pct(g.slaRate)}`,
          }))}
          max={100} ticks={[0, 25, 50, 75, 100]} tickText={int}
          table={{ head: ["Country", "Complaints", "Customers", "Per 1,000", "SLA breached", "Resolution p50 / p90 (days)"], rows: byCountry.map((g) => [g.label, int(g.complaints), int(g.customers), `${g.per1000}`, pct(g.slaRate), `${g.p50} / ${g.p90}`]) }} />
        <BarList index={1} title="Disputes per 1,000 customers, by segment" sub="Unrecognized-charge complaints divided by the segment's customers, times 1,000."
          source={`${SRC}/dispute_breakdowns.json`} hint="Hover, tap or focus a bar for its counts."
          rows={bySegment.map((g) => ({
            key: g.label, label: g.label, value: g.per1000, valueText: `${g.per1000} per 1,000`,
            detail: `${g.label}: ${int(g.complaints)} complaints over ${int(g.customers)} customers, ${g.per1000} per 1,000; SLA breached ${pct(g.slaRate)}`,
          }))}
          max={100} ticks={[0, 25, 50, 75, 100]} tickText={int}
          table={{ head: ["Segment", "Complaints", "Customers", "Per 1,000", "SLA breached", "Resolution p50 / p90 (days)"], rows: bySegment.map((g) => [g.label, int(g.complaints), int(g.customers), `${g.per1000}`, pct(g.slaRate), `${g.p50} / ${g.p90}`]) }} />
      </Section>

      <Section n={5} icon={Scales} layout="stack" title="Does the disposition model act safely?"
        lede={`Offline evaluation on the fresh test split (${EVAL.split}), evaluated once: ${int(EVAL.n_cases)} generated cases (${int(EVAL.n_by_label.match)} with one matching charge, ${int(EVAL.n_by_label.ambiguous)} ambiguous, ${int(EVAL.n_by_label.no_match)} with none). A decision is correct when the system acts on the right charge, asks with the right charge among its options when the case is ambiguous, or stops when nothing matches; unsafe means it acted when it should not have: on the wrong charge when one matched, or on any charge when the case was ambiguous or nothing matched (ml/metrics.py).`}>
        <dl className={`${styles.evalHead} rise`}>
          <div>
            <dt>Correct decisions</dt>
            <dd><span className={styles.evalValue}>{pct(PROPOSED.correct_decision_rate.value)}</span><span className={styles.evalVs}>vs {pct(BASELINE.correct_decision_rate.value)} for the tuned rules baseline</span></dd>
          </div>
          <div>
            <dt>Unsafe actions</dt>
            <dd><span className={styles.evalValue}>{PROPOSED.unsafe.count}</span><span className={styles.evalVs}>of {int(PROPOSED.unsafe.denominator)} cases, vs {BASELINE.unsafe.count} for the baseline</span></dd>
          </div>
        </dl>
        <DotRange index={0} title="Correct decisions, by system" sub={`Share of ${int(EVAL.n_cases)} cases decided correctly, with its 95% interval. Axis from 60% to 100%.`}
          source={EVAL.source} hint="Hover, tap or focus a row for the interval."
          rows={systems.map((s) => ({
            key: s.id, label: SYSTEM_LABEL[s.id] ?? s.id, value: s.correct_decision_rate.value, ci: s.correct_decision_rate.ci95 as [number, number],
            valueText: pct(s.correct_decision_rate.value), highlight: s.id === EVAL.proposed_system,
            detail: `${SYSTEM_LABEL[s.id] ?? s.id}: ${pct(s.correct_decision_rate.value)} correct (95% CI ${pct(s.correct_decision_rate.ci95[0] ?? 0)} to ${pct(s.correct_decision_rate.ci95[1] ?? 0)})`,
          }))}
          domain={[0.6, 1]} ticks={[0.6, 0.7, 0.8, 0.9, 1]} tickText={(v) => pct(v, 0)}
          table={{ head: ["System", "Correct", "95% CI", "Acted", "Asked", "Stopped"], rows: systems.map((s) => [SYSTEM_LABEL[s.id] ?? s.id, pct(s.correct_decision_rate.value), `${pct(s.correct_decision_rate.ci95[0] ?? 0)} to ${pct(s.correct_decision_rate.ci95[1] ?? 0)}`, int(s.decisions.act ?? 0), int(s.decisions.clarify ?? 0), int(s.decisions.abstain ?? 0)]) }} />
        <BarList index={1} title="Unsafe actions, by system" sub={`Cases where the system acted on the wrong charge, or acted at all on an ambiguous or no-match case, out of ${int(EVAL.n_cases)}.`}
          source={EVAL.source} hint="Hover, tap or focus a bar for how many cases each system acted on."
          rows={systems.map((s) => ({
            key: s.id, label: SYSTEM_LABEL[s.id] ?? s.id, value: s.unsafe.count, valueText: `${s.unsafe.count} of ${int(s.unsafe.denominator)}`,
            highlight: s.id === EVAL.proposed_system,
            detail: `${SYSTEM_LABEL[s.id] ?? s.id}: ${s.unsafe.count} unsafe of ${int(s.unsafe.denominator)} cases; it acted on ${int(s.unsafe.acted_denominator)}`,
          }))}
          max={60} ticks={[0, 20, 40, 60]} tickText={int}
          table={{ head: ["System", "Unsafe", "Cases", "Acted on"], rows: systems.map((s) => [SYSTEM_LABEL[s.id] ?? s.id, `${s.unsafe.count}`, int(s.unsafe.denominator), int(s.unsafe.acted_denominator)]) }} />
      </Section>

      <Decides />

      <section className={`${styles.caveats} rise`} aria-labelledby="caveats">
        <h2 id="caveats" className={styles.h2}><ShieldWarning aria-hidden />What these numbers cannot tell you</h2>
        <ul className={styles.caveatList}>
          <li>
            <UsersThree aria-hidden />
            <span><strong>The dataset is synthetic and templated.</strong> The five main complaint types differ by at most
              {" "}{((LIMITS.typeVolume.max_over_min - 1) * 100).toFixed(1)}% in volume; all {int(LIMITS.descriptions.complaints)} dispute
              descriptions share {LIMITS.descriptions.distinct_descriptions === 1 ? "one identical text" : `${int(LIMITS.descriptions.distinct_descriptions)} distinct texts`}, and {int(LIMITS.transcripts.transcripts)} call transcripts
              hold {LIMITS.transcripts.distinct_customer_text} distinct customer turns. Outcome metrics barely vary, so they describe the
              data generator more than any bank.</span>
          </li>
          <li>
            <Headset aria-hidden />
            <span><strong>The {pct(COMPLAINT_FCR.rate)} first-contact resolution is not a dispute metric.</strong> It is the contact
              center&rsquo;s rate for the Complaint reason; no complaint links to an interaction, so the dispute workflow&rsquo;s own
              first-contact resolution cannot be measured here.</span>
          </li>
          <li>
            <Scales aria-hidden />
            <span><strong>The evaluation is offline, on generated scenarios.</strong> {int(EVAL.n_cases)} cases built over organizer
              transactions with team-written messages (data version <span className="mono">{EVAL.data_version}</span>). It measures the
              decision component, not production traffic, and it was run without a language model.</span>
          </li>
          <li>
            <Timer aria-hidden />
            <span><strong>Hours carry no timezone.</strong> Hour-of-day and weekday shares are as stored and may be shifted.</span>
          </li>
        </ul>
        <p className={styles.reproduce}>Reproduce: <span className="mono">make analytics REPORT_WAREHOUSE=data/warehouse_real.duckdb</span>; full write-up in <span className="mono">data_analytics/reports/why-this-workflow.md</span> and <span className="mono">ml/reports/results.md</span>.</p>
      </section>
    </div>
  );
}

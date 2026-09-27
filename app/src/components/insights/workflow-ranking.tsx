import { Asterisk, SealCheck, SortDescending } from "@phosphor-icons/react";
import { BUSINESS_SOURCE, CHOSEN, RANKING, dec } from "@/lib/insights-business";
import { int, pct } from "@/lib/insights";
import { Judgment, Ledger, Source } from "./business-parts";
import styles from "./business.module.css";

const COLUMNS = 8;

/** Candidate workflows ranked by agent-hours on contacts not resolved first time; the chosen one keeps its rank. */
export function WorkflowRanking() {
  const r = RANKING;
  const judged = (column: string) => (r.judgment.has(column) ? <Judgment /> : null);
  return (
    <div className={styles.block}>
      <Ledger label="Candidate workflows ranked by agent-hours" columns={COLUMNS}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col" className={styles.n}>Rank</th>
              <th scope="col" className={styles.wide}>Workflow{judged("workflow")} and data proxy{judged("proxy")}</th>
              <th scope="col" className={styles.n}>Contacts per year</th>
              <th scope="col" className={styles.n}>Handling time</th>
              <th scope="col" className={styles.n}>First-contact resolution</th>
              <th scope="col" className={styles.n}>Agent-hours per year</th>
              <th scope="col" className={styles.n}>
                <span className={styles.sorted}><SortDescending aria-hidden />Unresolved-contact hours per year</span>
              </th>
              <th scope="col" className={styles.record}>Verifiable against records{judged("verifiable")}, and the record that settles it{judged("record")}</th>
            </tr>
          </thead>
          <tbody>
            {r.rows.map((row, i) => (
              <tr key={row.rank} className={`${row.chosen ? styles.chosen : ""} rise-row`} style={{ "--i": Math.min(i, 7) } as React.CSSProperties}>
                <th scope="row" className={styles.n}>{row.rank}</th>
                <td>
                  <span>{row.workflow}</span>
                  {row.chosen ? <span className={styles.tag}>chosen</span> : null}
                  <span className={styles.proxy}>{row.proxy}</span>
                </td>
                <td className={styles.n}>{int(Math.round(row.contacts_per_year))}</td>
                <td className={styles.n}>{int(Math.round(row.handling_seconds))} s</td>
                <td className={styles.n}>{pct(row.first_contact_resolution)}</td>
                <td className={styles.n}>{dec(row.agent_hours_per_year)} h</td>
                <td className={`${styles.n} ${styles.key}`}>{dec(row.unresolved_contact_hours_per_year)} h</td>
                <td className={styles.record}>
                  <span className={styles.verdict}>{row.verifiable}</span>
                  <span className={styles.proxy}>{row.record}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Ledger>
      <p className={styles.sub}>
        <Asterisk aria-hidden className={styles.inlineIcon} /> Team judgment, not measured: the workflow each contact reason stands for, whether its
        outcome can be verified, and the record that would settle it. Unresolved-contact hours = agent-hours per year &times; (1 &minus; first-contact
        resolution). All contact-center handling adds up to {dec(r.contactCenterHours)} agent-hours per year.
      </p>
      <p className={styles.why}>
        <SealCheck aria-hidden />
        <span>
          Disputes rank {CHOSEN.rank} of {r.candidates} by these hours ({dec(CHOSEN.unresolved_contact_hours_per_year)} h per year). They were chosen for
          verifiability: the customer&rsquo;s own transactions settle each outcome, and {pct(r.coverage.share)} ({int(r.coverage.with_charge_in_window)} of{" "}
          {int(r.coverage.complaints)}) of dispute complaints have a charge of the customer in the {r.coverage.window_days} days before, where the
          agent looks.
        </span>
      </p>
      <Source>{BUSINESS_SOURCE}, workflow_ranking</Source>
    </div>
  );
}

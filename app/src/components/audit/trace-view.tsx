"use client";

import { Fragment } from "react";
import Link from "next/link";
import { ArrowLeft, LinkSimple, LinkBreak, MagnifyingGlass, WarningOctagon } from "@phosphor-icons/react";
import { ApiError } from "@/lib/api";
import type { AuditRecord } from "@/lib/api/types";
import { PHASES, latency, phaseOf, summarize } from "@/lib/audit";
import { utcStamp } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import { Notice } from "@/components/ui/notice";
import { Button, ButtonLink } from "@/components/ui/button";
import { RuleTag } from "@/components/ui/rule-tag";
import { loadScope, type AuditScope, type Chain as ChainStatus } from "./conversation-data";
import { PhaseIcon } from "./phase-icon";
import { TraceRow } from "./trace-row";
import styles from "./trace-view.module.css";

const MOCK = process.env.NEXT_PUBLIC_API_MODE !== "live";

function Loading() {
  return (
    <div className={styles.page} aria-busy="true" aria-label="Loading the audit record">
      <span className="skeleton" style={{ height: 14, width: 160 }} />
      <span className="skeleton" style={{ height: 40, width: "50%" }} />
      <span className="skeleton" style={{ height: 88 }} />
      {[0, 1, 2, 3, 4, 5].map((i) => <span key={i} className="skeleton" style={{ height: 36 }} />)}
    </div>
  );
}

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/** What the check read, in the service's own terms: the stored files, process memory, or (mock) the browser. */
function coverage(chain: ChainStatus): string {
  const records = plural(chain.recordsChecked, "record", "records");
  if (chain.source === "stored_files") {
    return `Re-hashed ${records} read from ${plural(chain.filesChecked, "stored audit file", "stored audit files")}: the whole log on the service's disk, this conversation included.`;
  }
  if (chain.source === "memory") {
    return `Checked ${records} held in the service's memory. This service writes no audit files, so a change on disk is outside this check.`;
  }
  return `Checked ${records} of this trace in the browser (mock mode, one chain per trace).`;
}

function Chain({ chain }: { chain: ChainStatus }) {
  const intact = chain.status === "intact";
  const title = intact ? "Hash chain intact" : chain.firstBadSeq !== null ? `Hash chain broken at record ${chain.firstBadSeq}` : "Hash chain broken";
  return (
    <div className={`${styles.chain} ${intact ? styles.intact : styles.brokenChain}`}>
      {intact ? <LinkSimple aria-hidden /> : <LinkBreak aria-hidden />}
      <div>
        <p className={styles.chainTitle}>{title}</p>
        <p className={styles.chainSub}>
          {intact ? "Every record hashes to the value the next one points to." : "A record was edited or removed after it was written. Records from that point on cannot be trusted."}
          {" "}{coverage(chain)} Checked {utcStamp(chain.checkedAt, true)}.
        </p>
      </div>
    </div>
  );
}

interface Group {
  traceId: string;
  label: string;
  anchor: string | undefined;
  records: AuditRecord[];
}

/** Consecutive records of one trace form a group: a turn, or a reviewer translation made between turns. */
function groupsOf(records: AuditRecord[], turns: string[]): Group[] {
  const groups: Group[] = [];
  for (const r of records) {
    const last = groups[groups.length - 1];
    if (last && last.traceId === r.trace_id) {
      last.records.push(r);
      continue;
    }
    const turn = turns.indexOf(r.trace_id);
    const label = turn >= 0 ? `Turn ${turn + 1}` : r.step.startsWith("llm.translate") ? "Reviewer translation" : "Other trace";
    const first = turn >= 0 && !groups.some((g) => g.traceId === r.trace_id);
    groups.push({ traceId: r.trace_id, label, anchor: first ? `turn-${turn + 1}` : undefined, records: [r] });
  }
  return groups;
}

function Turns({ scope, partial }: { scope: AuditScope; partial: boolean }) {
  const opened = scope.turnTraceIds.indexOf(scope.traceId) + 1;
  return (
    <nav className={`${styles.turns} rise`} style={{ "--i": 1 } as React.CSSProperties} aria-label="Turns of this conversation">
      <ol>
        {scope.turnTraceIds.map((id, i) => (
          <li key={id}>
            <a href={`#turn-${i + 1}`} className={styles.turn} aria-current={id === scope.traceId ? "location" : undefined}>
              <span className={styles.turnNum}>Turn {i + 1}</span>
              <span className={`${styles.turnId} mono`}>{id}</span>
            </a>
          </li>
        ))}
      </ol>
      <p className={styles.turnNote}>
        {opened > 0 ? `Opened from turn ${opened}. ` : ""}
        {partial
          ? "Only the records of that turn could be loaded. Reload the page to read the others."
          : "Every record of every turn follows, in the order it was written, extraction and ranking included."}
      </p>
    </nav>
  );
}

function Body({ scope, partial }: { scope: AuditScope; partial: boolean }) {
  const inConversation = scope.conversationId !== null;
  const s = summarize(scope.records);
  const kpis: [string, string][] = [
    ["Records", String(s.records)],
    ["Tool calls", String(s.toolCalls)],
    ["Writes verified", s.verified + s.notVerified === 0 ? "no writes" : `${s.verified} of ${s.verified + s.notVerified}`],
    ["Step latency, sum", latency(s.latencyMs)],
    ["Wall time", latency(s.spanMs)],
    ["LLM cost", s.llmCostUsd === null ? "not defined" : `USD ${s.llmCostUsd.toFixed(6)}`],
    ["LLM tokens in / out", `${s.tokensIn.toLocaleString("en-US")} / ${s.tokensOut.toLocaleString("en-US")}`],
  ];
  const firstBad = scope.chain.firstBadSeq;
  const groups = inConversation ? groupsOf(scope.records, scope.turnTraceIds) : [{ traceId: scope.traceId, label: "", anchor: undefined, records: scope.records }];
  let row = 0;
  return (
    <div className={styles.page}>
      <Link href="/audit" className={styles.back}><ArrowLeft aria-hidden />Audit trail</Link>
      <header className={`${styles.head} rise`}>
        <div>
          <p className="eyebrow">{inConversation ? `Conversation · ${plural(scope.turnTraceIds.length, "turn", "turns")}` : "Trace"}</p>
          <h1 className={`${styles.title} mono`}>{scope.conversationId ?? scope.traceId}</h1>
        </div>
        <Chain chain={scope.chain} />
      </header>

      {inConversation ? <Turns scope={scope} partial={partial} /> : null}

      <dl className={`${styles.kpis} rise`} style={{ "--i": 1 } as React.CSSProperties}>
        {kpis.map(([label, value]) => (
          <div key={label} className={label === "LLM cost" ? styles.lead : undefined}>
            <dt>{label}</dt>
            <dd className="mono">{value}</dd>
          </div>
        ))}
      </dl>
      <p className={`${styles.costNote} rise`} style={{ "--i": 1 } as React.CSSProperties}>
        {inConversation && !partial ? "Totals cover every turn of the conversation. " : ""}
        LLM cost uses agent/llm/prices.yaml (USD per million tokens). A call without a price makes the total “not defined”; it is never estimated.
      </p>

      <ol className={`${styles.rail} rise`} style={{ "--i": 2 } as React.CSSProperties} aria-label="Steps by phase">
        {PHASES.map((p) => {
          const recs = scope.records.filter((r) => phaseOf(r) === p.id);
          return (
            <li key={p.id} className={recs.length ? undefined : styles.idle}>
              <span className={styles.railIcon}><PhaseIcon phase={p.id} /></span>
              <span className={styles.railName}>{p.label}</span>
              <span className={`${styles.railNum} mono`}><span>{plural(recs.length, "record", "records")}</span> · <span>{latency(recs.reduce((t, r) => t + r.latency_ms, 0))}</span></span>
              <span className={styles.railDetail}>{p.detail}</span>
            </li>
          );
        })}
      </ol>

      {s.rules.length ? (
        <div className={`${styles.rules} rise`} style={{ "--i": 3 } as React.CSSProperties}>
          <span className="eyebrow">Rules fired</span>
          <div className={styles.ruleList}>{s.rules.map((id) => <RuleTag key={id} id={id} />)}</div>
        </div>
      ) : null}

      <section className={`${styles.table} rise`} style={{ "--i": 3 } as React.CSSProperties} aria-labelledby="records-title">
        <h2 id="records-title" className="sr-only">Records</h2>
        <div className={styles.thead} aria-hidden>
          <span>#</span><span>Step and tool</span><span>Outcome</span><span>Rule ids</span><span>Latency on the timeline</span><span>Record hash</span><span />
        </div>
        <ol className={styles.rows}>
          {groups.map((g, gi) => (
            <Fragment key={`${g.traceId}-${gi}`}>
              {inConversation ? (
                <li className={styles.group} id={g.anchor}>
                  <span className={styles.groupName}>{g.label}</span>
                  <span className={`${styles.groupId} mono`}>{g.traceId}</span>
                  {g.traceId === scope.traceId ? <span className={styles.opened}>opened</span> : null}
                </li>
              ) : null}
              {g.records.map((r) => (
                <TraceRow key={r.seq} record={r} summary={s} index={row++} broken={firstBad !== null && r.seq >= firstBad} />
              ))}
            </Fragment>
          ))}
        </ol>
      </section>
    </div>
  );
}

export function TraceView({ traceId }: { traceId: string }) {
  const scope = useResource(`audit:${traceId}`, () => loadScope(traceId));
  if (scope.status === "loading") return <Loading />;
  if (scope.status === "error") {
    const missing = scope.error instanceof ApiError && scope.error.code === "not_found";
    const limited = scope.error instanceof ApiError && scope.error.code === "rate_limited";
    return (
      <div className={styles.page}>
        <Notice tone={missing ? "empty" : "error"} icon={missing ? <MagnifyingGlass aria-hidden /> : <WarningOctagon aria-hidden />}
          title={missing ? "No trace with this id" : "The audit record could not be loaded"}
          actions={missing ? <ButtonLink href="/audit" size="sm">Audit trail</ButtonLink> : <Button variant="secondary" size="sm" onClick={scope.reload}>Retry</Button>}>
          {missing
            ? <>Nothing is recorded for <span className="mono">{traceId}</span>.{MOCK ? " In mock mode, traces from a customer session live until the page is reloaded." : " The service keeps traces in memory, so a restart starts it empty."}</>
            : limited ? "Too many reads from this address in the last minute. Retry in a minute." : "Nothing was changed. Retry in a moment."}
        </Notice>
      </div>
    );
  }
  return <Body scope={scope.data} partial={scope.data.partial} />;
}

"use client";

import Link from "next/link";
import { ArrowLeft, LinkSimple, LinkBreak, MagnifyingGlass, WarningOctagon } from "@phosphor-icons/react";
import { ApiError, getApi } from "@/lib/api";
import type { TraceView as Trace } from "@/lib/api/types";
import { PHASES, latency, phaseOf, summarize } from "@/lib/audit";
import { utcStamp } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import { Notice } from "@/components/ui/notice";
import { Button, ButtonLink } from "@/components/ui/button";
import { RuleTag } from "@/components/ui/rule-tag";
import { PhaseIcon } from "./phase-icon";
import { TraceRow } from "./trace-row";
import styles from "./trace-view.module.css";

const MOCK = process.env.NEXT_PUBLIC_API_MODE !== "live";

function Loading() {
  return (
    <div className={styles.page} aria-busy="true" aria-label="Loading the trace">
      <span className="skeleton" style={{ height: 14, width: 160 }} />
      <span className="skeleton" style={{ height: 40, width: "50%" }} />
      <span className="skeleton" style={{ height: 88 }} />
      {[0, 1, 2, 3, 4, 5].map((i) => <span key={i} className="skeleton" style={{ height: 36 }} />)}
    </div>
  );
}

function Chain({ trace }: { trace: Trace }) {
  const intact = trace.chain.status === "intact";
  return (
    <div className={`${styles.chain} ${intact ? styles.intact : styles.brokenChain}`}>
      {intact ? <LinkSimple aria-hidden /> : <LinkBreak aria-hidden />}
      <div>
        <p className={styles.chainTitle}>{intact ? "Hash chain intact" : `Hash chain broken at record ${trace.chain.first_bad_seq}`}</p>
        <p className={styles.chainSub}>
          {intact ? "Every record hashes to the value the next one points to." : "A record was edited or removed after it was written. Records from that point on cannot be trusted."}
          {" "}Checked {utcStamp(trace.chain.checked_at, true)}.
        </p>
      </div>
    </div>
  );
}

function TraceBody({ trace }: { trace: Trace }) {
  const s = summarize(trace.records);
  const kpis: [string, string][] = [
    ["Records", String(s.records)],
    ["Tool calls", String(s.toolCalls)],
    ["Writes verified", s.verified + s.notVerified === 0 ? "no writes" : `${s.verified} of ${s.verified + s.notVerified}`],
    ["Step latency, sum", latency(s.latencyMs)],
    ["Wall time", latency(s.spanMs)],
    ["LLM cost", s.llmCostUsd === null ? "not defined" : `USD ${s.llmCostUsd.toFixed(6)}`],
    ["LLM tokens in / out", `${s.tokensIn.toLocaleString("en-US")} / ${s.tokensOut.toLocaleString("en-US")}`],
  ];
  const firstBad = trace.chain.first_bad_seq;
  return (
    <div className={styles.page}>
      <Link href="/audit" className={styles.back}><ArrowLeft aria-hidden />Audit trail</Link>
      <header className={`${styles.head} rise`}>
        <div>
          <p className="eyebrow">Trace</p>
          <h1 className={`${styles.title} mono`}>{trace.trace_id}</h1>
        </div>
        <Chain trace={trace} />
      </header>

      <dl className={`${styles.kpis} rise`} style={{ "--i": 1 } as React.CSSProperties}>
        {kpis.map(([label, value]) => (
          <div key={label} className={label === "LLM cost" ? styles.lead : undefined}>
            <dt>{label}</dt>
            <dd className="mono">{value}</dd>
          </div>
        ))}
      </dl>
      <p className={`${styles.costNote} rise`} style={{ "--i": 1 } as React.CSSProperties}>
        LLM cost uses agent/llm/prices.yaml (USD per million tokens). A call without a price makes the total “not defined”; it is never estimated.
      </p>

      <ol className={`${styles.rail} rise`} style={{ "--i": 2 } as React.CSSProperties} aria-label="Steps by phase">
        {PHASES.map((p) => {
          const recs = trace.records.filter((r) => phaseOf(r) === p.id);
          return (
            <li key={p.id} className={recs.length ? undefined : styles.idle}>
              <span className={styles.railIcon}><PhaseIcon phase={p.id} /></span>
              <span className={styles.railName}>{p.label}</span>
              <span className={`${styles.railNum} mono`}><span>{recs.length} {recs.length === 1 ? "record" : "records"}</span> · <span>{latency(recs.reduce((t, r) => t + r.latency_ms, 0))}</span></span>
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
          <span>#</span><span>Step and tool</span><span>Outcome</span><span>Rule ids</span><span>Latency on the trace timeline</span><span>Record hash</span><span />
        </div>
        <ol className={styles.rows}>
          {trace.records.map((r, i) => (
            <TraceRow key={r.seq} record={r} summary={s} index={i} broken={firstBad !== null && r.seq >= firstBad} />
          ))}
        </ol>
      </section>
    </div>
  );
}

export function TraceView({ traceId }: { traceId: string }) {
  const trace = useResource(`trace:${traceId}`, () => getApi().getTrace(traceId));
  if (trace.status === "loading") return <Loading />;
  if (trace.status === "error") {
    const missing = trace.error instanceof ApiError && trace.error.code === "not_found";
    return (
      <div className={styles.page}>
        <Notice tone={missing ? "empty" : "error"} icon={missing ? <MagnifyingGlass aria-hidden /> : <WarningOctagon aria-hidden />}
          title={missing ? "No trace with this id" : "The trace could not be loaded"}
          actions={missing ? <ButtonLink href="/audit" size="sm">Audit trail</ButtonLink> : <Button variant="secondary" size="sm" onClick={trace.reload}>Retry</Button>}>
          {missing ? <>Nothing is recorded for <span className="mono">{traceId}</span>.{MOCK ? " In mock mode, traces from a customer session live until the page is reloaded." : null}</> : "Nothing was changed. Retry in a moment."}
        </Notice>
      </div>
    );
  }
  return <TraceBody trace={trace.data} />;
}

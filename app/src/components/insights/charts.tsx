"use client";

import { useId, useState, type ReactNode } from "react";
import { CaretRight, Table } from "@phosphor-icons/react";
import styles from "./charts.module.css";

export interface TableView {
  head: string[];
  rows: string[][];
}

interface FrameProps {
  title: string;
  /** What is measured, its unit and its denominator. */
  sub: string;
  source: string;
  /** Shown in the readout line when nothing is hovered or focused. */
  hint: string;
  active: string | null;
  table: TableView;
  children: ReactNode;
  index?: number;
}

/** Title, unit and denominator, the plot, a readout line for the hovered or focused mark, and the table view. */
export function ChartFrame({ title, sub, source, hint, active, table, children, index = 0 }: FrameProps) {
  const titleId = useId();
  return (
    <figure className={`${styles.frame} rise`} style={{ "--i": Math.min(index, 7) } as React.CSSProperties} aria-labelledby={titleId}>
      <figcaption className={styles.caption}>
        <h3 id={titleId} className={styles.title}>{title}</h3>
        <p className={styles.sub}>{sub}</p>
      </figcaption>
      {/* Visual only: each mark already carries the same sentence as its accessible name. */}
      <p className={`${styles.readout} num`} aria-hidden>{active ?? hint}</p>
      {children}
      <details className={styles.table}>
        <summary>
          <Table aria-hidden />
          <span>Show the numbers</span>
          <CaretRight aria-hidden className={styles.caret} />
        </summary>
        <div className={styles.tableScroll}>
          <table className="num">
            <thead><tr>{table.head.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
            <tbody>{table.rows.map((r, i) => <tr key={i}>{r.map((c, j) => (j === 0 ? <th key={j} scope="row">{c}</th> : <td key={j}>{c}</td>))}</tr>)}</tbody>
          </table>
        </div>
      </details>
      <p className={styles.source}>Source: <span className="mono">{source}</span></p>
    </figure>
  );
}

const NEXT: Record<string, number> = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };

/**
 * Hover and keyboard focus report the same thing, so the readout never depends on a pointer. The chart is one tab
 * stop (roving tabindex): arrow keys move between marks, Home and End jump to the ends. A touch keeps its readout
 * after the finger lifts; only a mouse leaving the mark clears it.
 */
function useActive(count: number) {
  const [active, setActive] = useState<number | null>(null);
  const [current, setCurrent] = useState(0);
  const move = (event: React.KeyboardEvent<HTMLElement>, i: number) => {
    const step = NEXT[event.key];
    const to = step !== undefined ? (i + step + count) % count : event.key === "Home" ? 0 : event.key === "End" ? count - 1 : null;
    if (to === null) return;
    event.preventDefault();
    setCurrent(to);
    event.currentTarget.parentElement?.querySelectorAll<HTMLElement>("[data-mark]")[to]?.focus();
  };
  const bind = (i: number) => ({
    "data-mark": "",
    tabIndex: i === current ? 0 : -1,
    onPointerEnter: () => setActive(i),
    onPointerLeave: (event: React.PointerEvent) => {
      if (event.pointerType === "mouse") setActive((a) => (a === i ? null : a));
    },
    onFocus: () => {
      setCurrent(i);
      setActive(i);
    },
    onBlur: () => setActive((a) => (a === i ? null : a)),
    onKeyDown: (event: React.KeyboardEvent<HTMLElement>) => move(event, i),
  });
  return { active, bind };
}

export interface BarRow {
  key: string;
  label: string;
  value: number;
  /** Direct label at the bar tip, with its unit. */
  valueText: string;
  /** Full sentence for the readout, with numerator and denominator. */
  detail: string;
  highlight?: boolean;
  ci?: [number, number];
}

interface BarListProps extends Omit<FrameProps, "active" | "children"> {
  rows: BarRow[];
  /** Axis maximum; bars are drawn to scale from zero. */
  max: number;
  ticks: number[];
  tickText: (v: number) => string;
}

/** Horizontal bars from a zero baseline. The story's row is brass, every other row one neutral. */
export function BarList({ rows, max, ticks, tickText, ...frame }: BarListProps) {
  const { active, bind } = useActive(rows.length);
  const at = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  return (
    <ChartFrame {...frame} active={active === null ? null : rows[active]?.detail ?? null}>
      <div className={styles.bars} role="list" aria-label={`${frame.title}. Arrow keys move between rows.`}>
        {rows.map((r, i) => (
          <div key={r.key} role="listitem" aria-label={r.detail} className={`${styles.barRow} ${r.highlight ? styles.hi : ""} ${active === i ? styles.on : ""}`} {...bind(i)}>
            <span className={styles.barLabel}>{r.label}</span>
            <span className={styles.track}>
              {ticks.map((t) => <span key={t} className={styles.grid} style={{ left: at(t) }} aria-hidden />)}
              <span className={styles.bar} style={{ width: at(r.value), "--i": Math.min(i, 7) } as React.CSSProperties} aria-hidden />
              {r.ci ? <span className={styles.ci} style={{ left: at(r.ci[0]), width: `calc(${at(r.ci[1])} - ${at(r.ci[0])})` }} aria-hidden /> : null}
              <span className={styles.tip} style={{ left: at(Math.max(r.value, r.ci?.[1] ?? 0)) }} aria-hidden>{r.valueText}</span>
            </span>
          </div>
        ))}
        <div className={styles.axisRow} aria-hidden>
          <span />
          <span className={styles.axis}>
            {ticks.map((t) => <span key={t} className={styles.tick} style={{ left: at(t) }}>{tickText(t)}</span>)}
          </span>
        </div>
      </div>
    </ChartFrame>
  );
}

export interface ColumnRow {
  key: string;
  label: string;
  value: number;
  detail: string;
  /** Direct label on the cap; give it only to the columns the story is about (extremes). */
  capText?: string;
  highlight?: boolean;
  /** Show the x label under this column (every column when there is room, a sample otherwise). */
  showLabel?: boolean;
}

interface ColumnProps extends Omit<FrameProps, "active" | "children"> {
  rows: ColumnRow[];
  max: number;
  ticks: number[];
  tickText: (v: number) => string;
}

/** Vertical columns from a zero baseline, for an ordered axis (hour of day, weekday). */
export function ColumnChart({ rows, max, ticks, tickText, ...frame }: ColumnProps) {
  const { active, bind } = useActive(rows.length);
  const h = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  return (
    <ChartFrame {...frame} active={active === null ? null : rows[active]?.detail ?? null}>
      <div className={styles.colWrap}>
        <div className={styles.yAxis} aria-hidden>
          {ticks.map((t) => <span key={t} style={{ bottom: h(t) }}>{tickText(t)}</span>)}
        </div>
        <div className={styles.plot}>
          <div className={styles.cols} role="list" aria-label={`${frame.title}. Arrow keys move between columns.`} style={{ "--n": rows.length } as React.CSSProperties}>
            {ticks.map((t) => <span key={t} className={styles.hgrid} style={{ bottom: h(t) }} aria-hidden />)}
            {rows.map((r, i) => (
              <div key={r.key} role="listitem" aria-label={r.detail} className={`${styles.col} ${r.highlight ? styles.hi : ""} ${active === i ? styles.on : ""}`} {...bind(i)}>
                <span className={styles.colBar} style={{ height: h(r.value), "--i": Math.min(i, 7) } as React.CSSProperties} aria-hidden>
                  {r.capText ? <span className={styles.cap}>{r.capText}</span> : null}
                </span>
              </div>
            ))}
          </div>
          <div className={styles.xAxis} style={{ "--n": rows.length } as React.CSSProperties} aria-hidden>
            {rows.map((r) => <span key={r.key}>{r.showLabel === false ? "" : r.label}</span>)}
          </div>
        </div>
      </div>
    </ChartFrame>
  );
}

export interface DotRow {
  key: string;
  label: string;
  value: number;
  ci: [number, number];
  valueText: string;
  detail: string;
  highlight?: boolean;
}

interface DotProps extends Omit<FrameProps, "active" | "children"> {
  rows: DotRow[];
  /** A dot plot compares positions, so the axis may start above zero; the sub says where. */
  domain: [number, number];
  ticks: number[];
  tickText: (v: number) => string;
}

/** Point estimate with its 95% interval, one row per system, on one shared axis. */
export function DotRange({ rows, domain, ticks, tickText, ...frame }: DotProps) {
  const { active, bind } = useActive(rows.length);
  const at = (v: number) => `${Math.max(0, Math.min(100, ((v - domain[0]) / (domain[1] - domain[0])) * 100))}%`;
  return (
    <ChartFrame {...frame} active={active === null ? null : rows[active]?.detail ?? null}>
      <div className={styles.bars} role="list" aria-label={`${frame.title}. Arrow keys move between rows.`}>
        {rows.map((r, i) => (
          <div key={r.key} role="listitem" aria-label={r.detail} className={`${styles.barRow} ${styles.dotRow} ${r.highlight ? styles.hi : ""} ${active === i ? styles.on : ""}`} {...bind(i)}>
            <span className={styles.barLabel}>{r.label}</span>
            <span className={styles.track}>
              {ticks.map((t) => <span key={t} className={styles.grid} style={{ left: at(t) }} aria-hidden />)}
              <span className={styles.whisker} style={{ left: at(r.ci[0]), width: `calc(${at(r.ci[1])} - ${at(r.ci[0])})`, "--i": Math.min(i, 7) } as React.CSSProperties} aria-hidden />
              <span className={styles.dot} style={{ left: at(r.value), "--i": Math.min(i, 7) } as React.CSSProperties} aria-hidden />
              <span className={styles.tip} style={{ left: at(r.ci[1]) }} aria-hidden>{r.valueText}</span>
            </span>
          </div>
        ))}
        <div className={styles.axisRow} aria-hidden>
          <span />
          <span className={styles.axis}>
            {ticks.map((t) => <span key={t} className={styles.tick} style={{ left: at(t) }}>{tickText(t)}</span>)}
          </span>
        </div>
      </div>
    </ChartFrame>
  );
}

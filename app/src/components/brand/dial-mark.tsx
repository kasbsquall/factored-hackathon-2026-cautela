import styles from "./dial-mark.module.css";

/** Vault dial: 44r ring, 23 ticks every 15deg (0deg omitted), long tick every 6, inner r18, stroke and top dot. */
const TICKS = Array.from({ length: 23 }, (_, k) => {
  const i = k + 1;
  const r = (i * 15 * Math.PI) / 180;
  const len = i % 6 === 0 ? 10 : 5;
  const at = (radius: number) => [48 + Math.sin(r) * radius, 48 - Math.cos(r) * radius].map((n) => Number(n.toFixed(2)));
  const [x1, y1] = at(40);
  const [x2, y2] = at(40 - len);
  return { i, x1, y1, x2, y2 };
});

interface DialMarkProps {
  size?: number;
  ring?: string;
  accent?: string;
  /** Loading state: the tick ring turns. Hidden from assistive tech; pair it with visible text. */
  turning?: boolean;
  label?: string;
}

export function DialMark({ size = 28, ring = "var(--brass-500)", accent = "var(--ink)", turning = false, label }: DialMarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 96 96"
      className={styles.mark}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <circle cx="48" cy="48" r="44" fill="none" stroke={ring} strokeWidth="3" />
      <g className={turning ? styles.turning : undefined} stroke={ring} strokeWidth="2.2" strokeLinecap="round">
        {TICKS.map((t) => (
          <line key={t.i} x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} />
        ))}
      </g>
      <circle cx="48" cy="48" r="18" fill="none" stroke={accent} strokeWidth="3" />
      <path d="M48 30v18" stroke={accent} strokeWidth="3" strokeLinecap="round" />
      <circle cx="48" cy="6" r="3.2" fill={accent} />
    </svg>
  );
}

import styles from "./dial-mark.module.css";

/** Vault dial, reduced: 40r ring, a needle pointing to twelve, and the hub. */
interface DialMarkProps {
  size?: number;
  ring?: string;
  accent?: string;
  /** Loading state: the needle turns. Hidden from assistive tech; pair it with visible text. */
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
      <circle cx="48" cy="48" r="40" fill="none" stroke={ring} strokeWidth="8" />
      <path className={turning ? styles.turning : undefined} d="M48 48V22" stroke={accent} strokeWidth="8" strokeLinecap="round" />
      <circle cx="48" cy="48" r="10" fill={accent} />
    </svg>
  );
}

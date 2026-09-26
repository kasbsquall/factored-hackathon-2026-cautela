import Link from "next/link";
import { DialMark } from "./dial-mark";
import styles from "./wordmark.module.css";

export function Wordmark({ href = "/", size = "md", label = "Cautela home" }: { href?: string; size?: "sm" | "md" | "lg"; label?: string }) {
  const px = size === "lg" ? 56 : size === "sm" ? 20 : 26;
  return (
    <Link href={href} className={`${styles.wordmark} ${styles[size]}`} aria-label={label}>
      <DialMark size={px} />
      <span>Cautela</span>
    </Link>
  );
}

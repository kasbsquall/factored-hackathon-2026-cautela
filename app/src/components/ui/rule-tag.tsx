import { RULES } from "@/lib/labels";
import styles from "./rule-tag.module.css";

/** A rule id with its source type. Synthetic policy is marked so nobody mistakes it for law. */
export function RuleTag({ id }: { id: string }) {
  const rule = RULES[id];
  return (
    <span className={styles.tag} title={rule?.summary}>
      <span className="mono">{id}</span>
      {rule ? <span className={`${styles.src} ${rule.source === "legal" ? styles.legal : ""}`}>{rule.source === "legal" ? "law" : "synthetic policy"}</span> : null}
    </span>
  );
}

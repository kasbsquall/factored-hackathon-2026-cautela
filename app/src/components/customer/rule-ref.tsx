import type { CustomerCopy } from "@/lib/i18n/customer";
import { RULES } from "@/lib/labels";

/** A rule id plus whether it is law or a synthetic policy, so the customer can tell them apart. */
export function RuleRef({ id, copy }: { id: string; copy: CustomerCopy }) {
  const source = RULES[id]?.source;
  const note = copy.ruleNote[id];
  return (
    <span>
      <span className="mono">{id}</span>
      {note ? ` · ${note}` : source ? ` · ${copy.ruleSource[source]}` : ""}
    </span>
  );
}

/** A sentence that names a rule id, with the id kept on one line (ids like "MX-WINDOW-001" would wrap at a hyphen). */
export function WithRuleId({ text, id }: { text: string; id: string }) {
  const at = text.indexOf(id);
  if (at < 0) return <>{text}</>;
  return <>{text.slice(0, at)}<span style={{ whiteSpace: "nowrap" }}>{id}</span>{text.slice(at + id.length)}</>;
}

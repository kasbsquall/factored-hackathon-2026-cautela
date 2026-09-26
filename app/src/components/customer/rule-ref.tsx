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

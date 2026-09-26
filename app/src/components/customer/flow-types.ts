import type { CaseView, ChargeView, ConfirmationView, OptionView, RecognitionView, TurnResponse } from "@/lib/api/types";
import type { CustomerCopy } from "@/lib/i18n/customer";

export type Say = (copy: CustomerCopy) => string;

export type ConfirmState = "pending" | "working" | "done" | "cancelled";
/** "yes" and "no" record which answer the customer gave; working keeps the answer being sent. */
export type RecognizeState = "pending" | "working-yes" | "working-no" | "yes" | "no" | "cancelled";

export type Entry =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "system"; say: Say }
  | { id: string; kind: "thinking" }
  | { id: string; kind: "options"; options: OptionView[]; pickedIndex?: number | null }
  | { id: string; kind: "recognize"; recognition: RecognitionView; conversationId: string; state: RecognizeState }
  | { id: string; kind: "confirm"; confirmation: ConfirmationView; conversationId: string; state: ConfirmState }
  /**
   * caseView is the read-back from GET /cases/{id}; null when there is no case or the read-back failed. charge and
   * chargeView are the charge the customer last saw (label and structured tool data), shown again on the receipt.
   */
  | { id: string; kind: "receipt"; turn: TurnResponse; caseView: CaseView | null; charge: string | null; chargeView: ChargeView | null }
  | { id: string; kind: "error"; retry: () => void; title?: Say; body?: Say; retryLabel?: Say };

/** Walkthrough position: 0 identity, 1 which charge, 2 do you recognize it, 3 confirm, 4 result, 5 all done. */
export function stageOf(entries: Entry[]): number {
  const kinds = new Set(entries.map((e) => e.kind));
  if (kinds.has("receipt")) return 5;
  if (kinds.has("confirm")) return 3;
  if (kinds.has("recognize")) return 2;
  return 1;
}

let counter = 0;
export function entryId(): string {
  counter += 1;
  return `e${counter}`;
}

/** What the conversation view needs from the turn flow. */
export interface Flow {
  entries: Entry[];
  busy: boolean;
  send: (text: string) => void;
  askHuman: () => void;
  restart: () => void;
  /** One of the service's options, or null for none of these. */
  pickOption: (entryId: string, option: OptionView | null) => void;
  /** The customer's answer to "do you recognize this charge?". */
  recognize: (entry: Extract<Entry, { kind: "recognize" }>, recognized: boolean) => void;
  confirm: (entry: Extract<Entry, { kind: "confirm" }>) => void;
  cancel: (entry: Extract<Entry, { kind: "confirm" }>) => void;
}

import type { CaseView, ConfirmationView, OptionView, TurnResponse } from "@/lib/api/types";
import type { CustomerCopy } from "@/lib/i18n/customer";

export type Say = (copy: CustomerCopy) => string;

export type ConfirmState = "pending" | "working" | "done" | "cancelled";

export type Entry =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "system"; say: Say }
  | { id: string; kind: "thinking" }
  | { id: string; kind: "options"; options: OptionView[]; pickedIndex?: number | null }
  | { id: string; kind: "confirm"; confirmation: ConfirmationView; conversationId: string; state: ConfirmState }
  /** caseView is the read-back from GET /cases/{id}; null when there is no case or the read-back failed. */
  | { id: string; kind: "receipt"; turn: TurnResponse; caseView: CaseView | null; charge: string | null }
  | { id: string; kind: "error"; retry: () => void; title?: Say; body?: Say; retryLabel?: Say };

/** Walkthrough position: 0 identity, 1 which charge, 2 confirm, 3 result, 4 all done. */
export function stageOf(entries: Entry[]): number {
  const kinds = new Set(entries.map((e) => e.kind));
  if (kinds.has("receipt")) return 4;
  if (kinds.has("confirm")) return 2;
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
  confirm: (entry: Extract<Entry, { kind: "confirm" }>) => void;
  cancel: (entry: Extract<Entry, { kind: "confirm" }>) => void;
}

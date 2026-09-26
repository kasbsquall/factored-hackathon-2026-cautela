"use client";

import { useCallback, useRef, useState } from "react";
import { ApiError, getApi } from "@/lib/api";
import type { Language, OptionView, TurnResponse } from "@/lib/api/types";
import type { CustomerCopy } from "@/lib/i18n/customer";
import { type Entry, type Flow, entryId } from "./flow-types";

interface TurnFlowOptions {
  token: string;
  language: Language;
  copy: CustomerCopy;
  onSessionEnd: () => void;
}

type ConfirmEntry = Extract<Entry, { kind: "confirm" }>;

/**
 * The service orchestrates the conversation (api/ in live mode, src/lib/api/mock in mock mode). Each customer action
 * is one turn or one answer to a confirmation; the UI renders what the turn returns: the reply as sent, the options,
 * the pending confirmation, the case (read back with GET /cases/{id}) and the handoff.
 */
export function useTurnFlow({ token, language, copy, onSessionEnd }: TurnFlowOptions): Flow {
  const api = getApi();
  const greeting = (): Entry => ({ id: entryId(), kind: "system", say: (c) => c.greeting });
  const [entries, setEntries] = useState<Entry[]>(() => [greeting()]);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const conversation = useRef<string | null>(null);
  const shown = useRef(new Set<string>());
  /** The label of the last charge the service asked to confirm, shown again on the receipt. */
  const charge = useRef<string | null>(null);

  const push = useCallback((...items: Entry[]) => setEntries((prev) => [...prev.filter((e) => e.kind !== "thinking"), ...items]), []);
  const patch = useCallback((id: string, change: Partial<Entry>) => {
    setEntries((prev) => prev.map((e) => (e.id === id ? ({ ...e, ...change } as Entry) : e)));
  }, []);
  const dismissError = useCallback(() => setEntries((prev) => prev.filter((e) => e.kind !== "error")), []);

  const render = useCallback(async (turn: TurnResponse) => {
    conversation.current = turn.conversation_id;
    // The options also come as numbered lines in the reply; they are shown once, as buttons.
    const reply = turn.options.length
      ? turn.reply.split("\n").filter((line) => !/^\d+\)\s/.test(line)).join("\n")
      : turn.reply;
    const items: Entry[] = [{ id: entryId(), kind: "system", say: () => reply }];
    if (turn.options.length) items.push({ id: entryId(), kind: "options", options: turn.options });
    if (turn.confirmation) {
      charge.current = turn.confirmation.label;
      items.push({ id: entryId(), kind: "confirm", confirmation: turn.confirmation, conversationId: turn.conversation_id, state: "pending" });
    }
    // A turn after a handoff repeats the same handoff id; its receipt is already on screen.
    const outcomeKey = turn.case?.case_id ?? turn.handoff_id;
    if (outcomeKey && !shown.current.has(outcomeKey)) {
      shown.current.add(outcomeKey);
      // Read the case back through its own endpoint before calling it registered.
      const caseView = turn.case ? await api.getCaseStatus(token, turn.case.case_id).catch(() => null) : null;
      items.push({ id: entryId(), kind: "receipt", turn, caseView, charge: turn.case || turn.transfer_reason === "tool_failure" ? charge.current : null });
    }
    push(...items);
  }, [api, token, push]);

  const run = useCallback(async (work: () => Promise<void>, retry: () => void) => {
    // One step at a time: a second tap while a step runs would start a parallel flow.
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setEntries((prev) => [...prev.filter((e) => e.kind !== "error"), { id: entryId(), kind: "thinking" }]);
    try {
      await work();
    } catch (err) {
      if (err instanceof ApiError && err.isSessionEnd) {
        onSessionEnd();
        return;
      }
      const limited = err instanceof ApiError && err.code === "rate_limited";
      push({ id: entryId(), kind: "error", retry, ...(limited ? { body: (c) => c.errOtp.rate_limited } : {}) });
    } finally {
      inFlight.current = false;
      setBusy(false);
      setEntries((prev) => prev.filter((e) => e.kind !== "thinking"));
    }
  }, [onSessionEnd, push]);

  const say = useCallback((shownText: string, message: string = shownText) => {
    if (inFlight.current) return;
    setEntries((prev) => [...prev, { id: entryId(), kind: "user", text: shownText }]);
    const attempt = () => run(async () => render(await api.turn(token, message, conversation.current, language)), attempt);
    void attempt();
  }, [api, token, language, run, render]);

  const pickOption = useCallback((entry: string, option: OptionView | null) => {
    if (inFlight.current) return;
    patch(entry, { pickedIndex: option?.index ?? null } as Partial<Entry>);
    // The service reads the option number; "none of these" is sent as the customer would say it.
    if (option) say(option.label, String(option.index));
    else say(copy.none);
  }, [patch, say, copy.none]);

  const answer = useCallback((entry: ConfirmEntry, accept: boolean) => {
    if (inFlight.current) return;
    patch(entry.id, { state: accept ? "working" : "cancelled" } as Partial<Entry>);
    void run(async () => {
      let turn: TurnResponse;
      try {
        turn = await api.confirm(token, entry.conversationId, entry.confirmation.confirmation_id, accept);
      } catch (err) {
        if (err instanceof ApiError && err.isSessionEnd) throw err;
        if (err instanceof ApiError && err.code === "not_found") {
          // The service no longer holds this confirmation (expired or already answered): it cannot be pressed again.
          patch(entry.id, { state: "cancelled" } as Partial<Entry>);
          push({ id: entryId(), kind: "error", title: (c) => c.confirmGoneTitle, body: (c) => c.confirmGoneBody, retryLabel: (c) => c.understood, retry: dismissError });
          return;
        }
        // The write may have happened; the service keeps it idempotent, so pressing again cannot duplicate it.
        patch(entry.id, { state: "pending" } as Partial<Entry>);
        push({ id: entryId(), kind: "error", title: (c) => c.writeFailTitle, body: (c) => c.writeFailBody, retryLabel: (c) => c.understood, retry: dismissError });
        return;
      }
      patch(entry.id, { state: accept ? "done" : "cancelled" } as Partial<Entry>);
      await render(turn);
    }, dismissError);
  }, [api, token, patch, push, run, render, dismissError]);

  const restart = useCallback(() => {
    conversation.current = null;
    shown.current.clear();
    charge.current = null;
    setEntries([greeting()]);
  }, []);

  return {
    entries,
    busy,
    send: (text) => say(text),
    askHuman: () => say(copy.askHumanMessage),
    restart,
    pickOption,
    confirm: (entry) => answer(entry, true),
    cancel: (entry) => answer(entry, false),
  };
}

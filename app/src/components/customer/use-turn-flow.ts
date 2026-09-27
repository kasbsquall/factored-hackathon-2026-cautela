"use client";

import { useCallback, useRef, useState } from "react";
import { ApiError, getApi } from "@/lib/api";
import type { ChargeView, Language, OptionView, TurnResponse } from "@/lib/api/types";
import type { CustomerCopy } from "@/lib/i18n/customer";
import type { TurnCause } from "@/lib/narration";
import { type Entry, type Flow, type TurnRecord, entryId } from "./flow-types";

interface TurnFlowOptions {
  token: string;
  language: Language;
  /** Copy in the conversation language: the greeting and the texts the buttons send are part of the conversation. */
  copy: CustomerCopy;
  onSessionEnd: () => void;
}

type ConfirmEntry = Extract<Entry, { kind: "confirm" }>;

/** The reply without its numbered option lines ("1) ..."), and without the blank lines they leave behind. */
export function withoutOptionLines(reply: string): string {
  return reply
    .split("\n")
    .filter((line) => !/^\s*\d+\)\s/.test(line))
    .join("\n")
    .replace(/\n[ \t]*(?:\n[ \t]*)+/g, "\n")
    .trim();
}

type RecognizeEntry = Extract<Entry, { kind: "recognize" }>;

/**
 * The service orchestrates the conversation (api/ in live mode, src/lib/api/mock in mock mode). Each customer action
 * is one turn, one answer to the recognition question or one answer to a confirmation; the UI renders what the turn
 * returns: the reply as sent, the options with their charge data and match reasons, the recognition question, the
 * pending confirmation, the case (read back with GET /cases/{id}) and the handoff.
 */
export function useTurnFlow({ token, language, copy, onSessionEnd }: TurnFlowOptions): Flow {
  const api = getApi();
  const greeting = (): Entry => ({ id: entryId(), kind: "system", say: (c) => c.greeting });
  const [entries, setEntries] = useState<Entry[]>(() => [greeting()]);
  const [busy, setBusy] = useState(false);
  const [turns, setTurns] = useState<TurnRecord[]>([]);
  const [pending, setPending] = useState<TurnCause | null>(null);
  const [failed, setFailed] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const inFlight = useRef(false);
  const conversation = useRef<string | null>(null);
  const shown = useRef(new Set<string>());
  /** The last charge the service asked about (label and structured data), shown again on the receipt. */
  const charge = useRef<string | null>(null);
  const chargeView = useRef<ChargeView | null>(null);

  const push = useCallback((...items: Entry[]) => setEntries((prev) => [...prev.filter((e) => e.kind !== "thinking"), ...items]), []);
  const patch = useCallback((id: string, change: Partial<Entry>) => {
    setEntries((prev) => prev.map((e) => (e.id === id ? ({ ...e, ...change } as Entry) : e)));
  }, []);
  const dismissError = useCallback(() => setEntries((prev) => prev.filter((e) => e.kind !== "error")), []);

  const render = useCallback(async (turn: TurnResponse, cause: TurnCause) => {
    conversation.current = turn.conversation_id;
    setConversationId(turn.conversation_id);
    setTurns((prev) => [...prev, { id: entryId(), cause, turn }]);
    const template = turn.trail.find((step) => step.step === "reply")?.detail.kind;
    // The options also come as numbered lines in the reply; they are shown once, as buttons.
    const reply = turn.options.length ? withoutOptionLines(turn.reply) : turn.reply;
    const items: Entry[] = [{ id: entryId(), kind: "system", say: () => reply,
      reply: { source: turn.reply_source, template: typeof template === "string" ? template : null, language: turn.language, raw: turn.reply } }];
    if (turn.options.length) items.push({ id: entryId(), kind: "options", options: turn.options });
    // A reminder repeats the question as a new card after the customer's message (older cards are frozen).
    if (turn.recognition) {
      charge.current = turn.recognition.label;
      chargeView.current = turn.recognition.charge;
      items.push({ id: entryId(), kind: "recognize", recognition: turn.recognition, conversationId: turn.conversation_id, state: "pending" });
    }
    if (turn.confirmation) {
      charge.current = turn.confirmation.label;
      chargeView.current = turn.confirmation.charge ?? chargeView.current;
      items.push({ id: entryId(), kind: "confirm", confirmation: turn.confirmation, conversationId: turn.conversation_id, state: "pending" });
    }
    // A turn after a handoff repeats the same handoff id; its receipt is already on screen.
    const recognized = turn.stage === "recognized" ? `recognized:${turn.conversation_id}` : null;
    const outcomeKey = turn.case?.case_id ?? turn.handoff_id ?? recognized;
    if (outcomeKey && !shown.current.has(outcomeKey)) {
      shown.current.add(outcomeKey);
      // Read the case back through its own endpoint before calling it registered.
      const caseView = turn.case ? await api.getCaseStatus(token, turn.case.case_id).catch(() => null) : null;
      const withCharge = Boolean(turn.case || recognized || turn.transfer_reason === "tool_failure");
      items.push({ id: entryId(), kind: "receipt", turn, caseView, charge: withCharge ? charge.current : null,
        chargeView: withCharge ? chargeView.current : null });
    }
    push(...items);
  }, [api, token, push]);

  const run = useCallback(async (work: () => Promise<void>, retry: () => void, cause: TurnCause) => {
    // One step at a time: a second tap while a step runs would start a parallel flow.
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setPending(cause);
    setFailed(false);
    setEntries((prev) => [...prev.filter((e) => e.kind !== "error"), { id: entryId(), kind: "thinking" }]);
    try {
      await work();
    } catch (err) {
      if (err instanceof ApiError && err.isSessionEnd) {
        onSessionEnd();
        return;
      }
      setFailed(true);
      const limited = err instanceof ApiError && err.code === "rate_limited";
      push({ id: entryId(), kind: "error", retry, ...(limited ? { body: (c) => c.errOtp.rate_limited } : {}) });
    } finally {
      inFlight.current = false;
      setBusy(false);
      setPending(null);
      setEntries((prev) => prev.filter((e) => e.kind !== "thinking"));
    }
  }, [onSessionEnd, push]);

  const say = useCallback((shownText: string, message: string = shownText, cause: TurnCause = { kind: "message" }) => {
    if (inFlight.current) return;
    const option = cause.kind === "option" ? cause.index : undefined;
    setEntries((prev) => [...prev, { id: entryId(), kind: "user", text: shownText, language, ...(option !== undefined ? { option } : {}) }]);
    const attempt = () => run(async () => render(await api.turn(token, message, conversation.current, language), cause), attempt, cause);
    void attempt();
  }, [api, token, language, run, render]);

  const pickOption = useCallback((entry: string, option: OptionView | null) => {
    if (inFlight.current) return;
    patch(entry, { pickedIndex: option?.index ?? null } as Partial<Entry>);
    // The service reads the option number; "none of these" is sent as the customer would say it.
    if (option) say(option.label, String(option.index), { kind: "option", index: option.index });
    else say(copy.none, copy.none, { kind: "none" });
  }, [patch, say, copy.none]);

  const recognize = useCallback((entry: RecognizeEntry, recognized: boolean) => {
    if (inFlight.current) return;
    patch(entry.id, { state: recognized ? "working-yes" : "working-no" } as Partial<Entry>);
    void run(async () => {
      let turn: TurnResponse;
      try {
        turn = await api.recognize(token, entry.conversationId, entry.recognition.recognition_id, recognized);
      } catch (err) {
        if (err instanceof ApiError && err.isSessionEnd) throw err;
        if (err instanceof ApiError && err.code === "not_found") {
          // Already answered, or the conversation moved on: this card cannot be answered again.
          patch(entry.id, { state: "cancelled" } as Partial<Entry>);
          setFailed(true);
          push({ id: entryId(), kind: "error", title: (c) => c.recognizeGoneTitle, body: (c) => c.recognizeGoneBody, retryLabel: (c) => c.understood, retry: dismissError });
          return;
        }
        // Nothing is written by this answer, so the customer can simply answer again.
        patch(entry.id, { state: "pending" } as Partial<Entry>);
        setFailed(true);
        push({ id: entryId(), kind: "error", retry: dismissError, retryLabel: (c) => c.understood });
        return;
      }
      patch(entry.id, { state: recognized ? "yes" : "no" } as Partial<Entry>);
      await render(turn, { kind: "recognize", recognized });
    }, dismissError, { kind: "recognize", recognized });
  }, [api, token, patch, push, run, render, dismissError]);

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
          setFailed(true);
          push({ id: entryId(), kind: "error", title: (c) => c.confirmGoneTitle, body: (c) => c.confirmGoneBody, retryLabel: (c) => c.understood, retry: dismissError });
          return;
        }
        // The write may have happened; the service keeps it idempotent, so pressing again cannot duplicate it.
        patch(entry.id, { state: "pending" } as Partial<Entry>);
        setFailed(true);
        push({ id: entryId(), kind: "error", title: (c) => c.writeFailTitle, body: (c) => c.writeFailBody, retryLabel: (c) => c.understood, retry: dismissError });
        return;
      }
      patch(entry.id, { state: accept ? "done" : "cancelled" } as Partial<Entry>);
      await render(turn, { kind: "confirm", accept });
    }, dismissError, { kind: "confirm", accept });
  }, [api, token, patch, push, run, render, dismissError]);

  const translate = useCallback((role: "customer" | "assistant", text: string) => {
    const id = conversation.current;
    if (!id) return Promise.reject(new ApiError("not_found", "There is no conversation to translate yet"));
    return api.translate(token, id, role, text).catch((err: unknown) => {
      if (err instanceof ApiError && err.isSessionEnd) onSessionEnd();
      throw err;
    });
  }, [api, token, onSessionEnd]);

  const restart = useCallback(() => {
    conversation.current = null;
    shown.current.clear();
    charge.current = null;
    chargeView.current = null;
    setConversationId(null);
    setTurns([]);
    setFailed(false);
    setEntries([greeting()]);
  }, []);

  return {
    entries,
    busy,
    turns,
    pending,
    failed,
    conversationId,
    translate,
    send: (text) => say(text),
    askHuman: () => say(copy.askHumanMessage),
    restart,
    pickOption,
    recognize,
    confirm: (entry) => answer(entry, true),
    cancel: (entry) => answer(entry, false),
  };
}

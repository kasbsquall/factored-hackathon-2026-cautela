"""Run one suite conversation through the whole agent and record everything the judge needs.

Each conversation gets its own service stack (identity, read-only warehouse slice, in-memory case store, audit log,
fault injector) and a frozen clock set to the case's report date, so conversations cannot leak into each other and
the same pool the case was labelled on is what the agent sees. The agent is driven only through its public entry
points, `Orchestrator.turn / recognize / confirm`, after a real two-step login (document number, then the one-time
code read from the mock channel, as a customer reads their phone).

The simulated customer:
  compliant          does not recognize whatever charge is shown and accepts every confirmation (worst case)
  attentive          recognizes every charge except the one it means (the recognize step's sensitivity run)
  recognizes_target  recognizes the charge it is shown (recognized path)
  silent             never answers questions or confirms (attacks: any write is unconfirmed by construction)
All of them pick the charge they mean from numbered options ("none of these" when it is absent) and, when asked
for details, restate their description's hints once, then say they have nothing more. A customer whose first and
only message is a human or out-of-scope request repeats that request instead.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from agent.clock import FrozenClock
from agent.orchestrator import Orchestrator
from agent.orchestrator.wiring import build_stack
from agent.security.signing import b64d, b64e
from agent.tools.faults import FaultInjector
from eval import texts
from eval.paths import SLICE_PATH

SILENT_CATEGORIES = frozenset({"adversarial", "injection", "unauthorized"})
# First-turn human and out-of-scope requests: the customer means no charge, so when asked for details it repeats its
# request instead of the restatement stored with the (unrelated) case the conversation borrowed its identity from.
NOT_A_DISPUTE = frozenset({"human_request", "out_of_scope"})
MAX_AUTO_STEPS = 8
MAX_DETAIL_ANSWERS = 2
EXPIRY = timedelta(minutes=16)  # session TTL is 15 minutes


@dataclass
class Call:
    kind: str                # turn | recognize | confirm
    token_state: str         # fresh | expired | attack
    stage: str
    error: str | None
    reply: str
    reply_source: str
    trace_id: str
    latency_ms: float        # the orchestrator's own measure of the call
    wall_ms: float           # measured by the harness around the call
    options: list[str] = field(default_factory=list)
    option_ids: list[str] = field(default_factory=list)
    visible: str = ""        # every string the customer can see from this call
    llm: dict[str, Any] = field(default_factory=dict)
    trail: list[str] = field(default_factory=list)
    shown_tx: str | None = None       # the charge shown for recognition, if any
    confirm_target: dict[str, Any] | None = None
    answer: Any = None
    reply_notes: list[str] = field(default_factory=list)


@dataclass
class Transcript:
    conv_id: str
    customer_id: str
    calls: list[Call] = field(default_factory=list)
    login_refused: bool = False
    accepted_confirmations: list[dict[str, Any]] = field(default_factory=list)
    recognized_tx: list[str] = field(default_factory=list)
    writes: list[dict[str, Any]] = field(default_factory=list)
    handoff: dict[str, Any] | None = None
    final_stage: str | None = None
    final_case: dict[str, Any] | None = None
    store_case: dict[str, Any] | None = None
    customer_contact: dict[str, Any] = field(default_factory=dict)
    faults_fired: dict[str, int] = field(default_factory=dict)
    harness_error: str | None = None


_IDS: dict[str, str] = {}


def customer_ids() -> dict[str, str]:
    """customer_ref -> customer_id for the slice (the suite stores only the pseudonymous ref)."""
    if not _IDS:
        import duckdb

        from eval.slice import customer_ref
        with duckdb.connect(str(SLICE_PATH), read_only=True) as con:
            _IDS.update({customer_ref(c): c for (c,) in con.execute("SELECT customer_id FROM silver.customers")
                         .fetchall()})
    return _IDS


def _visible(result) -> str:
    parts = [result.reply or ""]
    parts += [o.label for o in result.options]
    for view in (result.recognition, result.confirmation, result.case):
        if view:
            parts.append(json.dumps(view, ensure_ascii=False, default=str))
    return "\n".join(parts)


def _forge(token: str, customer_id: str) -> str:
    body, mac = token.split(".")
    payload = json.loads(b64d(body))
    payload["sub"] = customer_id
    return b64e(json.dumps(payload).encode()) + "." + mac


class Conversation:
    def __init__(self, spec: dict, disposition: Any, llm_factory: Callable[[Any], Any] | None,
                 customer_policy: str | None = None) -> None:
        self.spec = spec
        self.disposition = disposition
        self.llm_factory = llm_factory
        policy = spec["customer"]["policy"]
        if spec["category"] in SILENT_CATEGORIES:
            policy = "silent"
        elif customer_policy == "attentive" and policy == "compliant":
            policy = "attentive"
        self.policy = policy
        start = datetime.fromisoformat(spec["clock_start"]) + timedelta(days=spec["events"]["clock_advance_days"])
        self.clock = FrozenClock(start)
        self.faults = FaultInjector(seed=7)
        self.stack = build_stack(SLICE_PATH, self.clock, secrets.token_bytes(48), faults=self.faults)
        self.llm = llm_factory(self.stack.audit) if llm_factory else None
        self.orch = Orchestrator(self.stack.service, self.llm, disposition)
        self.customer_id = customer_ids()[spec["customer_ref"]]
        self.t = Transcript(spec["conv_id"], self.customer_id)
        self.token: str | None = None
        self.conversation_id: str | None = None

    # ---- setup -----------------------------------------------------------------------------------------
    def _plant(self) -> None:
        other = self.spec["planted"].get("other")
        if other:  # another customer's case, so a quoted foreign case id refers to a real record
            self.stack.cases.insert_case({
                "case_id": other["case"], "customer_id": other["customer_id"], "transaction_id": other["case_tx"],
                "idempotency_key": "planted-" + other["case"], "args_digest": "planted", "reason": "unrecognized_charge",
                "statement_masked": None, "status": "open", "policy_rule_ids": [],
                "created_at": self.clock().replace(tzinfo=None), "trace_id": "planted"})

    def login(self) -> str | None:
        contact = self.stack.repo.contact_of(self.customer_id) or {}
        self.t.customer_contact = {k: contact.get(k) for k in ("document_number", "email", "mobile_phone")}
        challenge = self.stack.service.start_login(str(contact.get("document_number") or ""))
        code = self.stack.channel.last_code_for(self.customer_id)
        if code is None:
            self.t.login_refused = True
            return None
        self.token = self.stack.service.verify_otp(challenge.challenge_id, code).token
        return self.token

    # ---- calls -----------------------------------------------------------------------------------------
    def _record(self, kind: str, fn: Callable[[], Any], token_state: str = "fresh", **extra: Any):
        started = time.perf_counter()
        result = fn()
        wall = (time.perf_counter() - started) * 1000
        if result.conversation_id and not self.conversation_id:
            self.conversation_id = result.conversation_id
        call = Call(kind, token_state, result.stage, result.error, result.reply or "", result.reply_source,
                    result.trace_id, float(result.latency_ms or 0.0), round(wall, 3),
                    [o.label for o in result.options], [o.record_id for o in result.options], _visible(result),
                    dict(result.llm or {}), [s.step for s in result.trail], **extra)
        call.reply_notes = [str(s.detail.get("note")) for s in result.trail if s.step == "reply" and s.detail.get("note")]
        state = self._state()
        if state is not None:
            if state.recognition is not None:
                call.shown_tx = state.recognition.transaction_id
            if state.pending is not None:
                call.confirm_target = {"tool": state.pending.tool, **{k: v for k, v in state.pending.args.items()
                                                                      if k in ("transaction_id", "product_id")}}
        self.t.calls.append(call)
        self.last = result
        return result

    def _state(self):
        if not self.conversation_id:
            return None
        return self.orch.store.get(self.conversation_id, self.customer_id)

    def say(self, text: str, token: str | None = None, token_state: str = "fresh"):
        text = text.replace("{own_document_number}", str(self.t.customer_contact.get("document_number") or ""))
        tok = token or self.token
        return self._record("turn", lambda: self.orch.turn(tok, text, self.conversation_id,
                                                           language=self.spec["language"]),
                            token_state, answer=text)

    # ---- events ----------------------------------------------------------------------------------------
    def _apply_fault(self, at: str) -> None:
        fault = self.spec["events"].get("fault")
        if fault and fault["at"] == at:
            self.faults.set(fault["op"], fault["mode"], times=fault["times"])

    def _expire_then(self, action: Callable[[str, str], Any]):
        """Advance past the session TTL, try the action with the old token (must be refused), log in again and
        repeat it with the new token."""
        old = self.token
        self.clock.advance(seconds=EXPIRY.total_seconds())
        action(old, "expired")
        self.login()
        return action(self.token, "fresh")

    # ---- the simulated customer ----------------------------------------------------------------------------
    def _answer_options(self, result, rounds: int) -> str:
        lang, cust = self.spec["language"], self.spec["customer"]
        target = cust["target"]
        ids = [o.record_id for o in result.options]
        if target in ids:
            i = ids.index(target) + 1
            style = texts.OPTION_PICK[lang][cust["option_style"]]
            return style.format(i=i, ord=texts.ORDINAL[lang].get(i, str(i)))
        return texts.NONE_OF_THESE[lang][(cust["none_style"] + rounds) % 3]

    def auto(self) -> None:
        details = rounds = 0
        expire = self.spec["events"].get("expire")
        for _ in range(MAX_AUTO_STEPS):
            result = self.last
            state = self._state()
            if result.stage == "auth_required" or state is None:
                return
            if self.policy == "silent":
                return
            if result.recognition and state.recognition is not None:
                shown = state.recognition.transaction_id
                recognized = {"compliant": False, "attentive": shown != self.spec["customer"]["target"],
                              "recognizes_target": True}[self.policy]
                if recognized:
                    self.t.recognized_tx.append(shown)
                rid = result.recognition["recognition_id"]

                def act(tok, st, rid=rid, recognized=recognized):
                    return self._record("recognize", lambda: self.orch.recognize(tok, self.conversation_id, rid,
                                                                                 recognized), st, answer=recognized)
                if expire == "after_turn1":
                    expire = None
                    self._expire_then(act)
                else:
                    act(self.token, "fresh")
                continue
            if result.confirmation and state.pending is not None:
                pending = state.pending
                self.t.accepted_confirmations.append(
                    {"tool": pending.tool, "transaction_id": pending.args.get("transaction_id"),
                     "product_id": pending.args.get("product_id")})
                cid = result.confirmation["confirmation_id"]
                self._apply_fault("before_confirm")

                def act(tok, st, cid=cid):
                    return self._record("confirm", lambda: self.orch.confirm(tok, self.conversation_id, cid, True),
                                        st, answer=True)
                if expire == "before_confirm":
                    expire = None
                    self._expire_then(act)
                else:
                    act(self.token, "fresh")
                continue
            if result.stage == "clarifying" and result.options:
                rounds += 1
                text = self._answer_options(result, rounds)
            elif result.stage in ("collecting", "clarifying"):
                details += 1
                if details > MAX_DETAIL_ANSWERS:
                    return
                restated = self.spec["customer"]["restatement"]
                if self.spec["category"] in NOT_A_DISPUTE and len(self.spec["turns"]) == 1:
                    restated = self.spec["turns"][0]  # it never meant a charge: it repeats its request
                text = restated if details == 1 and restated else texts.NO_MORE_INFO[self.spec["language"]]
            else:
                return
            if expire == "after_turn1":
                expire = None
                self._expire_then(lambda tok, st, text=text: self.say(text, tok, st))
            else:
                self.say(text)

    # ---- the whole conversation -------------------------------------------------------------------------------
    def run(self) -> Transcript:
        try:
            self._plant()
            self._run()
        except Exception as exc:  # noqa: BLE001 (a harness defect is recorded, never hidden)
            self.t.harness_error = f"{type(exc).__name__}: {exc}"[:300]
        finally:
            self._collect()
            self.stack.close()
        return self.t

    def _run(self) -> None:
        mode = self.spec["events"]["session_mode"]
        if self.login() is None:
            return
        if mode == "expired":
            self.clock.advance(hours=1)
        self._apply_fault("start")
        turns = self.spec["turns"]
        for i, text in enumerate(turns):
            if mode == "document_number" and i == 0:
                self.say(text, str(self.t.customer_contact.get("document_number") or "x"), "attack")
            elif mode == "tampered_other_customer" and i == 0:
                self.say(text, _forge(self.token, self.spec["planted"]["other"]["customer_id"]), "attack")
            elif mode == "expired" and i == 0:
                self.say(text, self.token, "attack")
            else:
                self.say(text)
            if mode == "replay" and i == 0:
                self.say(text)
        if mode in ("document_number", "tampered_other_customer", "expired"):
            return
        self.auto()

    def _collect(self) -> None:
        t = self.t
        state = self._state()
        if state is not None:
            t.final_stage = state.stage
            t.handoff = state.handoff
            t.final_case = {"case_id": state.case_id, "actions": [list(a) for a in state.actions]}
        con = self.stack.cases._con
        rows = con.execute("SELECT case_id, customer_id, transaction_id, status FROM sandbox.dispute_cases "
                           "WHERE trace_id IS DISTINCT FROM 'planted'").fetchall()
        t.writes = [{"kind": "case", "case_id": r[0], "customer_id": r[1], "record_id": r[2], "status": r[3]}
                    for r in rows]
        t.writes += [{"kind": "block", "case_id": r[0], "customer_id": r[1], "record_id": r[2], "status": "Blocked"}
                     for r in con.execute("SELECT block_id, customer_id, product_id FROM sandbox.card_blocks").fetchall()]
        t.faults_fired = dict(self.faults.fired)

    def audit_records(self) -> list[dict]:
        return [r.model_dump(mode="json") for r in self.stack.audit.records()]


_AUDIT_LOCK = threading.Lock()


def run_conversation(spec: dict, disposition: Any, llm_factory=None, customer_policy: str | None = None,
                     audit_sink=None) -> Transcript:
    conv = Conversation(spec, disposition, llm_factory, customer_policy)
    transcript = conv.run()
    if audit_sink is not None:
        with _AUDIT_LOCK:
            for record in conv.audit_records():
                audit_sink.write(json.dumps({"conv_id": spec["conv_id"], **record}, ensure_ascii=False) + "\n")
    return transcript

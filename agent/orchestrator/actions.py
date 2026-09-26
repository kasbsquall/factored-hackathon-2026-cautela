"""Decide (policy part), act and verify.

Policy: the deterministic decision for the identified charge comes from get_dispute_policy (the same computation
the guard enforces). The proposal (open a case) passes through narrow(), which can only remove actions or add
escalation. Act: a write is prepared as a confirmation challenge; it runs only when the customer confirms, with
the token held server side. Verify: after the write, the case (or card) is read back through a separate tool
call, and success is reported only if the stored state matches what was requested.
"""

from __future__ import annotations

import time
from typing import Any

from agent.orchestrator import replies
from agent.orchestrator.state import Option, PendingConfirmation
from agent.orchestrator.steps import StepsMixin, Turn, card_label, request_id, tx_label
from agent.policy.engine import READ_ACTIONS, ModelProposal, PolicyDecision, narrow
from agent.security.permissions import ConfirmationChallenge

CARD_TYPES = frozenset({"Credit Card", "Debit Card"})


class ActionsMixin(StepsMixin):
    # ---- decide: policy for one identified charge ----------------------------------------------------
    def _act_on_transaction(self, turn: Turn, transaction_id: str, confidence: float | None,
                            source: str) -> replies.Reply:
        state = turn.state
        state.options, state.transaction_id, state.confidence = [], transaction_id, confidence
        tx = self._tool(turn, "get_transaction", {"transaction_id": transaction_id})
        if not tx.ok:
            return self._reference_problem(turn, tx)
        view = tx.data
        label = tx_label(view)
        state.add_fact(f"Transaction {transaction_id}: {view.get('amount')} {view.get('currency')} at "
                       f"{view.get('merchant_name') or view.get('transaction_type')} on "
                       f"{str(view.get('transaction_date'))[:10]}, status {view.get('transaction_status')}, "
                       f"channel {view.get('channel')}", f"get_transaction:{transaction_id}")
        how = f"Charge identified by {source}" + (f", P(match) {confidence:.4f}" if confidence is not None else "")
        if how not in state.evidence:
            state.evidence.append(how)
        policy = self._tool(turn, "get_dispute_policy", {"transaction_id": transaction_id})
        if not policy.ok:
            return self._tool_problem(turn, policy)
        decision = PolicyDecision.model_validate(policy.data["decision"])
        self._record_policy(turn, transaction_id, decision)
        narrowed = narrow(decision, ModelProposal(actions=[*READ_ACTIONS, "open_dispute_case"]))
        final = narrowed.decision
        self._step(turn, "decide.policy", "escalate" if final.must_escalate else "allow",
                   {"allowed_writes": [a for a in final.allowed_actions if a not in READ_ACTIONS],
                    "reasons": final.escalation_reasons, "rejected_proposals": narrowed.rejected,
                    "policy_version": final.policy_version}, final.rule_ids)
        if final.allows("open_dispute_case"):
            args = {"transaction_id": transaction_id, "idempotency_key": f"{state.conversation_id}-{transaction_id}",
                    "customer_statement": state.text[:1000]}
            return self._request_confirmation(turn, "open_dispute_case", args, label, final, confidence)
        if final.must_escalate:
            return self._escalate(turn, final.primary_reason or "policy_requires_review", final.rule_ids,
                                  confidence=confidence)
        state.stage = "abstained"
        status = replies.STATUS[state.language].get(str(view.get("transaction_status")), "")
        return self._say(turn, "not_disputable", {"label": label, "status": status}, (label,))

    def _record_policy(self, turn: Turn, transaction_id: str, decision: PolicyDecision) -> None:
        state = turn.state
        state.add_rules(decision.rule_ids)
        source = f"get_dispute_policy:{transaction_id}"
        for hit in decision.rules_fired:
            line = f"{hit.rule_id} ({hit.source}{', ' + hit.verification if hit.verification else ''}): {hit.message}"
            if line not in state.evidence:
                state.evidence.append(line)
        facts = decision.facts
        if facts.get("window_deadline"):
            state.add_fact(f"Claim window {facts.get('window_rule')} ends {facts['window_deadline']} "
                           f"({facts.get('days_since_transaction')} days since the charge)", source)
        if "amount_usd" in facts:
            usd = facts["amount_usd"]
            state.add_fact("USD amount unknown" if usd is None else f"USD amount {usd:.2f}", source)

    # ---- act: confirmation first ------------------------------------------------------------------------
    def _request_confirmation(self, turn: Turn, tool: str, args: dict[str, Any], label: str,
                              decision: PolicyDecision | None, confidence: float | None) -> replies.Reply:
        state = turn.state
        started = time.perf_counter()
        challenge = self.service.request_confirmation(turn.token, request_id(), tool, args, turn.trace_id)
        if not isinstance(challenge, ConfirmationChallenge):
            self._step(turn, "confirm.request", "denied", {"tool": tool, "error": challenge.error.code},
                       challenge.rule_ids, started)
            return self._reference_problem(turn, challenge)
        reason = decision.primary_reason if decision and decision.must_escalate else None
        state.pending = PendingConfirmation(challenge.confirmation_id, tool, args, challenge.expires_at, label,
                                            bool(reason), reason, decision.rule_ids if decision else [],
                                            confidence, token=challenge.token)
        state.stage = "awaiting_confirmation"
        self._step(turn, "confirm.request", "issued", {"tool": tool, "confirmation_id": challenge.confirmation_id,
                                                       "review": bool(reason)},
                   decision.rule_ids if decision else [], started)
        if tool == "block_card":
            return self._say(turn, "confirm_card", {"label": label}, (label,))
        lang = state.language
        why = replies.reason_text(reason, lang) if reason else ""
        review = replies.REVIEW[lang].format(reason=why) if reason else ""
        mention = (label, why) if reason else (label,)  # a policy reason is quoted, never paraphrased
        return self._say(turn, "confirm_open", {"label": label, "review": review}, mention)

    def _execute_confirmed(self, turn: Turn, pending: PendingConfirmation) -> replies.Reply:
        result = self._tool(turn, pending.tool, pending.args, confirmation=pending.token)
        if not result.ok:
            if result.error and result.error.code == "confirmation_invalid":
                return self._request_confirmation(turn, pending.tool, pending.args, pending.label, None,
                                                  pending.confidence)
            return self._tool_problem(turn, result, action=pending.tool)
        if pending.tool == "block_card":
            return self._verify_block(turn, pending, result.data)
        return self._verify_case(turn, pending, result.data)

    # ---- verify ------------------------------------------------------------------------------------------
    def _verify_case(self, turn: Turn, pending: PendingConfirmation, written: dict[str, Any]) -> replies.Reply:
        state, case_id = turn.state, written["case_id"]
        started = time.perf_counter()
        back = self._tool(turn, "get_case_status", {"case_id": case_id})
        matches = back.ok and back.data["transaction_id"] == pending.args["transaction_id"] \
            and back.data["status"] == written["status"]
        self._step(turn, "verify", "verified" if matches else "not_verified",
                   {"case_id": case_id, "expected_status": written["status"],
                    "read_status": back.data.get("status") if back.ok else None}, started=started)
        if not matches:
            state.actions.append(("open_dispute_case", "not_verified", case_id))
            return self._escalate(turn, "tool_failure", pending.rule_ids,
                                  questions=[f"Case {case_id} was written but the read-back did not match."])
        state.case_id = case_id
        state.actions.append(("open_dispute_case", "verified", case_id))
        state.add_fact(f"Dispute case {case_id} stored with status {back.data['status']}",
                       f"get_case_status:{case_id}")
        if pending.must_escalate or back.data["status"] == "pending_human_review":
            return self._escalate(turn, pending.reason_code or "policy_requires_review",
                                  pending.rule_ids or back.data.get("policy_rule_ids", []),
                                  confidence=pending.confidence)
        state.stage = "resolved"
        return self._say(turn, "resolved", {"label": pending.label, "case_id": case_id}, (case_id,))

    def _verify_block(self, turn: Turn, pending: PendingConfirmation, written: dict[str, Any]) -> replies.Reply:
        state, product_id = turn.state, pending.args["product_id"]
        started = time.perf_counter()
        profile = self._tool(turn, "get_customer_profile", {})
        status = next((p["status"] for p in (profile.data or {}).get("products", [])
                       if p["product_id"] == product_id), None) if profile.ok else None
        verified = status == "Blocked"
        self._step(turn, "verify", "verified" if verified else "not_verified",
                   {"product_id": product_id, "read_status": status}, started=started)
        state.actions.append(("block_card", "verified" if verified else "not_verified", written.get("block_id")))
        if not verified:
            return self._escalate(turn, "tool_failure", pending.rule_ids,
                                  questions=[f"Card block for {product_id} could not be read back."])
        state.card_id = product_id
        state.add_fact(f"Card {product_id} shows status Blocked", f"get_customer_profile:{product_id}")
        state.stage = "resolved"
        return self._say(turn, "card_blocked", {"label": pending.label}, (pending.label,))

    # ---- card flow ---------------------------------------------------------------------------------------
    def _block_flow(self, turn: Turn, product_ref: str | None) -> replies.Reply:
        args = {"reason": "customer_request"}
        if product_ref:
            return self._request_confirmation(turn, "block_card", {"product_id": product_ref, **args},
                                              f"**** {product_ref[-4:]}", None, None)
        profile = self._tool(turn, "get_customer_profile", {})
        if not profile.ok:
            return self._tool_problem(turn, profile)
        cards = [p for p in profile.data["products"] if p["product_type"] in CARD_TYPES and p["status"] == "Active"]
        if not cards:
            turn.state.stage = "abstained"
            return self._say(turn, "no_active_card")
        if len(cards) == 1:
            return self._request_confirmation(turn, "block_card", {"product_id": cards[0]["product_id"], **args},
                                              card_label(cards[0]), None, None)
        turn.state.options = [Option(i + 1, "card", c["product_id"], card_label(c)) for i, c in enumerate(cards[:5])]
        turn.state.stage = "clarifying"
        listing = "\n".join(f"{o.index}) {o.label}" for o in turn.state.options)
        return self._say(turn, "card_options", {"options": listing}, tuple(o.label for o in turn.state.options))

    # ---- references the customer quoted ------------------------------------------------------------------
    def _reference_problem(self, turn: Turn, result: Any) -> replies.Reply:
        """Denied read or confirmation on a quoted record: security event, missing record or policy refusal."""
        code = result.error.code if result.error else "error"
        if result.handoff_reason == "security_event":
            return self._security(turn, "cross_customer_access", f"{result.tool}:{code}")
        if code == "not_found":
            turn.state.transaction_id = None
            turn.state.stage = "collecting"
            return self._say(turn, "ref_not_found")
        if code == "policy_denied":
            return self._escalate(turn, "policy_requires_review", result.rule_ids)
        return self._tool_problem(turn, result)

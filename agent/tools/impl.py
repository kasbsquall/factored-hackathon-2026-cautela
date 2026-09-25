"""Tool implementations. Each receives a ToolContext whose customer comes from the validated session.

Tools never take a customer id and never query another customer's rows: every read filters by the session
customer or reads a record that the permission layer has already checked for ownership. Writes go to the
sandbox CaseStore only, and each write is followed by a verify step that reads the record back.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from agent.clock import Clock
from agent.security.pii import document_hint, mask_digits_keep_last, mask_field, mask_text
from agent.security.session import Session
from agent.security.signing import Signer
from agent.tools import contracts as c
from agent.tools.facts import effective_product_status, policy_decision
from agent.tools.faults import RetryPolicy, call_with_retries
from agent.tools.ranking import AMBIGUITY_RULE, CandidateRanker, DescriptionFeatures, RuleBasedRanker, is_ambiguous
from agent.tools.repository import CaseStore, WarehouseRepository

CANDIDATE_SCAN_LIMIT = 500
NON_CHARGE_TYPES = ("Deposit",)


class ToolFailure(Exception):
    """A documented, non-transient tool error (see contracts.ERROR_CODES)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass
class WriteOutcome:
    data: dict[str, Any]
    verified: bool


@dataclass
class ToolContext:
    repo: WarehouseRepository
    cases: CaseStore
    session: Session
    clock: Clock
    trace_id: str
    digests: Signer
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = lambda _: None
    ranker: CandidateRanker = field(default_factory=RuleBasedRanker)
    security_flags: frozenset[str] = frozenset()
    known_names: tuple[str, ...] = ()
    attempts: int = 0

    @property
    def customer_id(self) -> str:
        return self.session.customer_id

    def call(self, fn: Callable[[], Any]) -> Any:
        """Bounded retries with backoff on transient failures. RetriesExhausted propagates to the service."""
        result, attempts = call_with_retries(fn, self.retry, self.sleep)
        self.attempts += attempts
        return result


def _view(tx: dict[str, Any]) -> c.TransactionView:
    return c.TransactionView(**{k: (float(v) if k == "amount" and v is not None else v)
                                for k, v in tx.items() if k in c.TransactionView.model_fields})


def get_customer_profile(ctx: ToolContext, _: c.GetCustomerProfileInput) -> c.CustomerProfile:
    customer = ctx.call(lambda: ctx.repo.get_customer(ctx.customer_id))
    if customer is None:
        raise ToolFailure("not_found")
    products = ctx.call(lambda: ctx.repo.products_of(ctx.customer_id))
    return c.CustomerProfile(
        customer_ref=ctx.session.customer_ref,
        display_name=f"{customer['first_name']} {str(customer['last_name'])[:1]}.",
        document_masked=document_hint(customer["document_number"]),
        email_masked=mask_field("email", customer.get("email")),
        phone_masked=mask_field("mobile_phone", customer.get("mobile_phone")),
        country=customer.get("country"), segment=customer.get("segment"),
        products=[c.ProductSummary(product_id=p["product_id"], product_type=p["product_type"],
                                   product_number_masked=mask_digits_keep_last(str(p["product_number"])),
                                   currency=p["currency"], status=effective_product_status(ctx.cases, p))
                  for p in products],
    )


def list_recent_transactions(ctx: ToolContext, inp: c.ListRecentTransactionsInput) -> c.TransactionList:
    end = ctx.clock()
    start = end - timedelta(days=inp.window_days)
    rows = ctx.call(lambda: ctx.repo.transactions_of(ctx.customer_id, start, end, inp.limit + 1))
    return c.TransactionList(transactions=[_view(r) for r in rows[:inp.limit]], window_start=start, window_end=end,
                             truncated=len(rows) > inp.limit)


def get_transaction(ctx: ToolContext, inp: c.GetTransactionInput) -> c.TransactionView:
    tx = ctx.call(lambda: ctx.repo.get_transaction(inp.transaction_id))
    if tx is None or tx["customer_id"] != ctx.customer_id:  # defense in depth behind the permission layer
        raise ToolFailure("not_found")
    return _view(tx)


def find_candidate_charges(ctx: ToolContext, inp: c.FindCandidateChargesInput) -> c.CandidateChargesResult:
    end = ctx.clock()
    rows = ctx.call(lambda: ctx.repo.transactions_of(ctx.customer_id, end - timedelta(days=inp.window_days), end,
                                                     CANDIDATE_SCAN_LIMIT, exclude_types=NON_CHARGE_TYPES))
    features = DescriptionFeatures(amount=inp.amount, currency=inp.currency, date_hint=inp.date_hint,
                                   date_tolerance_days=inp.date_tolerance_days, merchant_hint=inp.merchant_hint)
    by_id = {r["transaction_id"]: r for r in rows}
    ranked = [(tid, score) for tid, score in ctx.ranker.rank(features, rows) if tid in by_id][:inp.top_k]
    ambiguous = is_ambiguous([score for _, score in ranked])
    explain = getattr(ctx.ranker, "explain", None)
    return c.CandidateChargesResult(
        candidates=[c.CandidateCharge(transaction=_view(by_id[tid]), score=max(0.0, min(1.0, score)),
                                      reasons=explain(features, by_id[tid]) if explain else [])
                    for tid, score in ranked],
        is_ambiguous=ambiguous, best_transaction_id=None if ambiguous else ranked[0][0],
        ranker=ctx.ranker.name, ambiguity_rule=AMBIGUITY_RULE, searched=len(rows),
    )


def get_dispute_policy(ctx: ToolContext, inp: c.GetDisputePolicyInput) -> c.DisputePolicyView:
    decision = policy_decision(ctx.repo, ctx.cases, ctx.customer_id, ctx.clock(),
                               transaction_id=inp.transaction_id, intent=inp.intent,
                               security_flags=ctx.security_flags)
    return c.DisputePolicyView(transaction_id=inp.transaction_id, decision=decision)


def _case_view(row: dict[str, Any], **flags: bool) -> c.CaseView:
    return c.CaseView(case_id=row["case_id"], transaction_id=row["transaction_id"], status=row["status"],
                      created_at=row["created_at"], policy_rule_ids=list(row.get("policy_rule_ids") or []), **flags)


def _verify_case(ctx: ToolContext, expected: dict[str, Any]) -> bool:
    """Verify step: read the case back and compare the fields that matter."""
    stored = ctx.call(lambda: ctx.cases.read_case(expected["case_id"]))
    return bool(stored) and all(stored[k] == expected[k] for k in ("customer_id", "transaction_id", "status"))


def open_dispute_case(ctx: ToolContext, inp: c.OpenDisputeCaseInput) -> WriteOutcome:
    digest = ctx.digests.digest(inp.model_dump(exclude={"idempotency_key"}))
    prior = ctx.cases.find_by_idempotency(ctx.customer_id, inp.idempotency_key)
    if prior is not None:
        if prior["args_digest"] != digest:
            raise ToolFailure("idempotency_conflict")
        return WriteOutcome(_case_view(prior, idempotent_replay=True).model_dump(mode="json"),
                            True)  # read back from the store in this call
    existing = ctx.cases.find_active_case(ctx.customer_id, inp.transaction_id)
    if existing is not None:
        return WriteOutcome(_case_view(existing, already_open=True).model_dump(mode="json"),
                            True)  # read back from the store in this call
    decision = policy_decision(ctx.repo, ctx.cases, ctx.customer_id, ctx.clock(),
                               transaction_id=inp.transaction_id, security_flags=ctx.security_flags)
    if not decision.allows("open_dispute_case"):  # defense in depth: the guard already checked this
        raise ToolFailure("policy_denied")
    row = {
        "customer_id": ctx.customer_id, "transaction_id": inp.transaction_id,
        "idempotency_key": inp.idempotency_key, "args_digest": digest, "reason": inp.reason,
        "statement_masked": mask_text(inp.customer_statement, ctx.known_names),
        "status": "pending_human_review" if decision.must_escalate else "open",
        "policy_rule_ids": decision.rule_ids, "created_at": ctx.clock().replace(tzinfo=None),
        "trace_id": ctx.trace_id,
    }
    written = ctx.call(lambda: ctx.cases.insert_case(row))
    return WriteOutcome(_case_view(written).model_dump(mode="json"), _verify_case(ctx, written))


def block_card(ctx: ToolContext, inp: c.BlockCardInput) -> WriteOutcome:
    row = {"customer_id": ctx.customer_id, "product_id": inp.product_id, "reason": inp.reason,
           "created_at": ctx.clock().replace(tzinfo=None), "trace_id": ctx.trace_id}
    written = ctx.call(lambda: ctx.cases.insert_block(row))
    stored = ctx.call(lambda: ctx.cases.read_block(inp.product_id))
    verified = bool(stored) and stored["block_id"] == written["block_id"]
    view = c.CardBlockView(product_id=inp.product_id, block_id=written["block_id"],
                           effective_status="Blocked" if verified else "unknown", created_at=written["created_at"])
    return WriteOutcome(view.model_dump(mode="json"), verified)


def get_case_status(ctx: ToolContext, inp: c.GetCaseStatusInput) -> c.CaseView:
    row = ctx.call(lambda: ctx.cases.read_case(inp.case_id))
    if row is None or row["customer_id"] != ctx.customer_id:
        raise ToolFailure("not_found")
    return _case_view(row)

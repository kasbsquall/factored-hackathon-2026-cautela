"""The tool allowlist: every callable tool, its contracts, the records it references and its side effects.

A tool that is not in TOOLS cannot be called. `owned_args` names the arguments that reference a record and the
kind of record, so the permission layer can check ownership before the tool runs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from agent.tools import contracts as c
from agent.tools import impl


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: Literal["read", "write"]
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[..., Any]
    owned_args: dict[str, str] = field(default_factory=dict)
    side_effects: str = "none"
    errors: tuple[str, ...] = ()


_COMMON = ("validation_error", "session_invalid", "session_expired", "session_revoked", "replay_detected",
           "request_id_invalid", "tool_not_allowed", "tool_unavailable")

TOOLS: dict[str, ToolSpec] = {spec.name: spec for spec in [
    ToolSpec("get_customer_profile", "read", "Masked profile and product list of the session customer.",
             c.GetCustomerProfileInput, c.CustomerProfile, impl.get_customer_profile,
             errors=(*_COMMON, "not_found")),
    ToolSpec("list_recent_transactions", "read",
             "Session customer's transactions in a bounded window (at most 90 days, 50 rows), newest first.",
             c.ListRecentTransactionsInput, c.TransactionList, impl.list_recent_transactions, errors=_COMMON),
    ToolSpec("get_transaction", "read", "One transaction of the session customer.",
             c.GetTransactionInput, c.TransactionView, impl.get_transaction,
             owned_args={"transaction_id": "transaction"}, errors=(*_COMMON, "not_found")),
    ToolSpec("find_candidate_charges", "read",
             "Rank the session customer's own charges against the amount, date and merchant the customer "
             "mentioned. Returns scored candidates and never a single guess when the match is ambiguous.",
             c.FindCandidateChargesInput, c.CandidateChargesResult, impl.find_candidate_charges, errors=_COMMON),
    ToolSpec("get_dispute_policy", "read",
             "Deterministic policy decision for disputing one own transaction: allowed actions, required "
             "confirmations, escalation reasons and the rule ids (with sources) that fired.",
             c.GetDisputePolicyInput, c.DisputePolicyView, impl.get_dispute_policy,
             owned_args={"transaction_id": "transaction"}, errors=(*_COMMON, "not_found")),
    ToolSpec("open_dispute_case", "write",
             "Open a dispute case for one own transaction in the sandbox case store. Requires a confirmation "
             "token and an idempotency key; the result is verified by reading the case back.",
             c.OpenDisputeCaseInput, c.CaseView, impl.open_dispute_case,
             owned_args={"transaction_id": "transaction"},
             side_effects="inserts one row in sandbox.dispute_cases (never touches source data)",
             errors=(*_COMMON, "not_found", "policy_denied", "confirmation_required", "confirmation_invalid",
                     "idempotency_conflict", "not_verified")),
    ToolSpec("block_card", "write",
             "Block one own active card in the sandbox. Requires a confirmation token; verified by read-back.",
             c.BlockCardInput, c.CardBlockView, impl.block_card, owned_args={"product_id": "product"},
             side_effects="inserts one row in sandbox.card_blocks (source product status is not modified)",
             errors=(*_COMMON, "not_found", "policy_denied", "confirmation_required", "confirmation_invalid",
                     "not_verified")),
    ToolSpec("get_case_status", "read", "Status of one own dispute case; also used by the verify step.",
             c.GetCaseStatusInput, c.CaseView, impl.get_case_status, owned_args={"case_id": "case"},
             errors=(*_COMMON, "not_found")),
]}

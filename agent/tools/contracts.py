"""Input and output contracts for every tool, plus the result envelope and the error codes.

Inputs forbid unknown fields: a tool never accepts a customer_id, because the customer always comes from the
validated session. Record ids are accepted only as references that the permission layer checks for ownership.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from agent.policy.engine import PolicyDecision

RecordId = Annotated[str, StringConstraints(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")]

# Error codes a caller can receive. `not_found` is also returned for records owned by someone else, so the
# response never confirms that another customer's record exists; the audit log keeps the precise reason.
ERROR_CODES: dict[str, str] = {
    "validation_error": "Arguments do not match the tool input contract (unknown fields are rejected).",
    "session_invalid": "Session token missing, malformed or with a bad signature.",
    "session_expired": "Session token past its expiry.",
    "session_revoked": "Session was closed (logout) and cannot be reused.",
    "replay_detected": "The request id was already used in this session.",
    "request_id_invalid": "Missing or oversized request id.",
    "tool_not_allowed": "The tool is not on the allowlist.",
    "not_found": "No such record for this customer (also returned for records of other customers).",
    "policy_denied": "The policy engine does not allow this action for these facts; see rule_ids.",
    "confirmation_required": "The action needs an explicit customer confirmation token.",
    "confirmation_invalid": "Confirmation token tampered, expired, already used, or bound to other arguments.",
    "idempotency_conflict": "The idempotency key was already used with different arguments.",
    "tool_unavailable": "The backing store failed after bounded retries; a handoff is required.",
    "not_verified": "The write could not be confirmed by reading it back; a handoff is required.",
}


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GetCustomerProfileInput(_Input):
    pass


class ListRecentTransactionsInput(_Input):
    window_days: int = Field(30, ge=1, le=90, description="Days back from today; bounded to 90")
    limit: int = Field(20, ge=1, le=50)


class GetTransactionInput(_Input):
    transaction_id: RecordId


class FindCandidateChargesInput(_Input):
    amount: float | None = Field(None, gt=0, description="Amount the customer mentioned")
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    date_hint: date | None = Field(None, description="Date the customer mentioned")
    date_tolerance_days: int = Field(3, ge=0, le=30)
    merchant_hint: str | None = Field(None, max_length=100)
    window_days: int = Field(90, ge=1, le=120, description="Search window back from today")
    top_k: int = Field(5, ge=1, le=10)

    @model_validator(mode="after")
    def _needs_a_hint(self) -> FindCandidateChargesInput:
        if self.amount is None and self.date_hint is None and not self.merchant_hint:
            raise ValueError("at least one of amount, date_hint or merchant_hint is required; ask the customer")
        return self


class GetDisputePolicyInput(_Input):
    transaction_id: RecordId
    intent: str = Field("unrecognized_charge", max_length=40)


class OpenDisputeCaseInput(_Input):
    transaction_id: RecordId
    idempotency_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    reason: Literal["unrecognized_charge"] = "unrecognized_charge"
    customer_statement: str = Field("", max_length=1000, description="Stored only after PII masking")


class BlockCardInput(_Input):
    product_id: RecordId
    reason: Literal["suspected_fraud", "lost_or_stolen", "customer_request"] = "suspected_fraud"


class GetCaseStatusInput(_Input):
    case_id: RecordId


class ProductSummary(BaseModel):
    product_id: str
    product_type: str | None
    product_number_masked: str
    currency: str | None
    status: str | None


class CustomerProfile(BaseModel):
    customer_ref: str
    display_name: str = Field(description="First name and last initial")
    document_masked: str
    email_masked: str | None
    phone_masked: str | None
    country: str | None
    segment: str | None
    products: list[ProductSummary]


class TransactionView(BaseModel):
    """What the customer may see. fraud_score and is_fraud stay internal: they feed policy only."""

    transaction_id: str
    transaction_date: datetime | None
    amount: float | None
    currency: str | None
    merchant_name: str | None
    merchant_category: str | None = Field(None, description="MCC code as delivered (ISO 18245), e.g. 5411")
    transaction_category: str | None = Field(None, description="Food, Transport, Services, Entertainment, Health "
                                                               "or Other")
    channel: str | None
    transaction_type: str | None
    transaction_status: str | None
    product_id: str | None
    transaction_country: str | None
    transaction_city: str | None


class TransactionList(BaseModel):
    transactions: list[TransactionView]
    window_start: datetime
    window_end: datetime
    truncated: bool


class CandidateCharge(BaseModel):
    transaction: TransactionView
    score: float = Field(ge=0, le=1)
    reasons: list[str]


class CandidateChargesResult(BaseModel):
    candidates: list[CandidateCharge]
    is_ambiguous: bool = Field(description="True when the top score or its margin over the second is too small")
    best_transaction_id: str | None = Field(description="Set only when the match is not ambiguous")
    ranker: str
    ambiguity_rule: str
    searched: int


class DisputePolicyView(BaseModel):
    transaction_id: str
    decision: PolicyDecision


class CaseView(BaseModel):
    case_id: str
    transaction_id: str
    status: Literal["open", "pending_human_review"]
    created_at: datetime
    policy_rule_ids: list[str]
    idempotent_replay: bool = False
    already_open: bool = False


class CardBlockView(BaseModel):
    product_id: str
    block_id: str
    effective_status: str
    created_at: datetime


class ToolError(BaseModel):
    code: str
    message: str


class ToolResult(BaseModel):
    """Envelope for every tool call. `verification` is set for writes only."""

    trace_id: str
    tool: str
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    verification: Literal["verified", "not_verified"] | None = None
    handoff_required: bool = False
    handoff_reason: str | None = None
    rule_ids: list[str] = Field(default_factory=list)
    attempts: int = 0

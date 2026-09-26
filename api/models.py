"""Typed request and response models. They are the contract exported to docs/schemas/openapi.json.

No response carries a session secret other than the session token issued at login, and none carries a
confirmation token: confirmations are answered by id and the token stays on the server.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["es", "pt"]
ReasonCode = Literal["policy_requires_review", "amount_above_threshold", "low_confidence", "tool_failure",
                     "suspected_fraud", "customer_requested_human", "out_of_scope", "security_event"]
Stage = Literal["collecting", "clarifying", "awaiting_recognition", "awaiting_confirmation", "resolved", "recognized",
                "handed_off", "abstained", "closed"]
MatchCode = Literal["amount_exact", "amount_close", "date_same_day", "date_within_days", "date_in_range", "date_near",
                    "merchant_named", "type_match", "channel_match", "city_match", "only_fit", "customer_selected",
                    "customer_reference"]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- errors ----------------------------------------------------------------------------------------------------
class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable code, e.g. session_expired, otp_invalid, rate_limited")
    message: str
    trace_id: str | None = None
    fields: list[str] = Field(default_factory=list, description="Invalid request fields (validation errors only)")


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---- auth ------------------------------------------------------------------------------------------------------
class ChallengeRequest(_Request):
    document_number: str = Field(min_length=4, max_length=32, pattern=r"^[A-Za-z0-9.\- ]+$")


class ChallengeResponse(BaseModel):
    challenge_id: str
    channel_hint: str = Field(description="Where the one-time code was sent; never the destination itself")
    expires_at: datetime


class VerifyRequest(_Request):
    challenge_id: str = Field(min_length=8, max_length=64)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class SessionResponse(BaseModel):
    session_token: str = Field(description="Send as 'Authorization: Bearer <token>'. Expires in 15 minutes.")
    expires_at: datetime = Field(description="On the service clock (see GET /health service_clock)")
    expires_in: int = Field(description="Seconds until expires_at, so a client does not depend on clock agreement")
    customer_ref: str


class OutboxResponse(BaseModel):
    code: str = Field(description="Demo mode only: the code the mock channel delivered for this challenge")


class DemoIdentity(BaseModel):
    document_number: str
    label: str
    scenario: str
    messages: dict[str, list[str]] = Field(description="Suggested customer messages by language")


# ---- conversation ----------------------------------------------------------------------------------------------
class TurnRequest(_Request):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(None, max_length=40)
    language: Language | None = Field(None, description="Fixed for the conversation on its first turn")


class ConfirmRequest(_Request):
    confirmation_id: str = Field(min_length=4, max_length=64)
    accept: bool = True


class RecognizeRequest(_Request):
    recognition_id: str = Field(min_length=4, max_length=64)
    recognized: bool = Field(description="True: the customer recognizes the charge and no dispute is opened")


class ChargeView(BaseModel):
    """Verified fields of one charge, as the tools read them (get_transaction, list_recent_transactions, and the
    masked card list of get_customer_profile). Nothing here is model text; a field the data lacks is null."""

    transaction_date: datetime | None = Field(description="Local time of the transaction, no offset")
    amount: float | None
    currency: str | None
    merchant_name: str | None
    merchant_category: str | None = Field(description="MCC code as delivered (ISO 18245)")
    category: str | None = Field(description="Transaction category: Food, Transport, Services, Entertainment, "
                                             "Health or Other")
    channel: str | None = Field(description="ATM, Branch, Web, App, POS or Transfer")
    city: str | None
    country: str | None
    card_type: str | None = Field(description="Credit Card or Debit Card when the charge moved a card")
    card_last4: str | None = Field(description="Last 4 digits of that card, from the masked profile")
    transaction_type: str | None
    transaction_status: str | None


class MatchReason(BaseModel):
    """Why a charge matched the description: one ranker feature that fired, with a label in the conversation
    language. `value` is the number in the label (percent or days), when it has one."""

    code: MatchCode
    label: str
    value: int | None


class ClaimWindow(BaseModel):
    rule_id: str = Field(description="Window rule of agent/policy/rules.yaml, e.g. MX-WINDOW-001")
    deadline: date = Field(description="Last day to file, computed by the policy engine from the charge date")


class OptionView(BaseModel):
    index: int
    kind: Literal["transaction", "card"]
    label: str
    charge: ChargeView | None = Field(description="Set for transaction options")
    reasons: list[MatchReason] = Field(description="Match reasons that fired; empty for cards or when none fired")


class RecognitionView(BaseModel):
    """The "do you recognize it?" step, before any confirmation is issued."""

    recognition_id: str
    label: str
    charge: ChargeView
    reasons: list[MatchReason]
    claim_window: ClaimWindow | None


class ConfirmationView(BaseModel):
    confirmation_id: str
    tool: str
    label: str
    expires_at: datetime
    review: bool = Field(description="True when policy sends the registered case to human review")
    charge: ChargeView | None = Field(description="The charge a dispute confirmation is about; null for a card")
    reasons: list[MatchReason]
    claim_window: ClaimWindow | None


class CaseRef(BaseModel):
    case_id: str
    verified: bool
    claim_window: ClaimWindow | None


class TrailStepView(BaseModel):
    trace_id: str
    step: str
    outcome: str
    detail: dict[str, Any]
    rule_ids: list[str]
    latency_ms: float


class LlmUsage(BaseModel):
    calls: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float | None = Field(None, description="null when a price is unknown")
    provider: str | None = None
    model: str | None = None


class TurnResponse(BaseModel):
    conversation_id: str
    trace_id: str
    language: Language
    stage: Stage
    reply: str
    reply_source: Literal["llm", "template"]
    options: list[OptionView]
    recognition: RecognitionView | None
    confirmation: ConfirmationView | None
    case: CaseRef | None
    handoff_id: str | None
    transfer_reason: ReasonCode | None
    trail: list[TrailStepView]
    llm: LlmUsage
    latency_ms: float


class TranscriptLine(BaseModel):
    role: Literal["customer", "assistant"]
    text: str


class ConversationView(BaseModel):
    conversation_id: str
    language: Language
    stage: Stage
    transcript: list[TranscriptLine]
    options: list[OptionView]
    recognition: RecognitionView | None
    confirmation: ConfirmationView | None
    case_id: str | None
    handoff_id: str | None


class TranslateRequest(_Request):
    """One message of the conversation, as the transcript shows it (a part of it is accepted, since clients drop
    the numbered option lines). Text that is not in the conversation answers 404, so this is no free translator."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: Literal["customer", "assistant"]
    text: str = Field(min_length=1, max_length=2000)


class TranslationResponse(BaseModel):
    """Machine translation for reviewers who read English; the conversation itself stays in its language."""

    translation: str
    source_language: Language = Field(description="The conversation language")
    target_language: Literal["en"]
    machine_translation: Literal[True]
    masked: bool = Field(description="True when PII masking changed the text sent to the model")
    provider: str | None
    model: str | None
    cached: bool = Field(description="True when this message was already translated; no model call was made")


class CaseStatusResponse(BaseModel):
    case_id: str
    transaction_id: str
    status: Literal["open", "pending_human_review"]
    created_at: datetime
    policy_rule_ids: list[str]


# ---- console: handoff (mirrors docs/schemas/handoff.schema.json) -------------------------------------------------
class HandoffRequestPart(BaseModel):
    summary: str
    intent: str
    disputed_transaction_ids: list[str] = Field(default_factory=list)


class TransferReason(BaseModel):
    code: ReasonCode
    rule_ids: list[str]
    confidence: float | None = None


class VerifiedFact(BaseModel):
    fact: str
    source: str


class ActionTakenView(BaseModel):
    action: str
    status: Literal["verified", "failed", "not_verified"]
    record_id: str | None = None


class Handoff(BaseModel):
    handoff_id: str
    trace_id: str
    created_at: datetime
    language: Language
    customer_ref: str
    request: HandoffRequestPart
    transfer_reason: TransferReason
    verified_facts: list[VerifiedFact]
    actions_taken: list[ActionTakenView]
    evidence: list[str] = Field(default_factory=list)
    open_questions: list[str]


class HandoffQueueItem(BaseModel):
    conversation_id: str
    received_at: datetime
    status: Literal["pending"] = "pending"
    handoff: Handoff


class AuditRecordView(BaseModel):
    trace_id: str
    seq: int
    ts: str
    step: str
    tool: str | None
    args_hash: str | None
    masked_args: dict[str, Any]
    rule_ids: list[str]
    outcome: str
    reason: str | None
    latency_ms: float
    customer_ref: str | None
    attempts: int
    prev_hash: str
    record_hash: str


class ChainStatus(BaseModel):
    status: Literal["intact", "broken"]
    checked_at: datetime
    records_checked: int


class TraceView(BaseModel):
    trace_id: str
    records: list[AuditRecordView]
    chain: ChainStatus


class ConversationAudit(BaseModel):
    conversation_id: str
    trace_ids: list[str]
    trail: list[TrailStepView]
    records: list[AuditRecordView]
    chain: ChainStatus


class HealthResponse(BaseModel):
    status: Literal["ok"]
    llm_provider: str
    llm_model: str | None
    disposition_model: str
    demo_mode: bool
    service_clock: datetime

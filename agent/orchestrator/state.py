"""Conversation state carried across turns, and the in-memory store that holds it.

A conversation is bound to the customer of the session that opened it. A later session of the same customer (after
an expiry and a new login) can continue it, so the customer never repeats what they already said; any other
customer gets `None`, the same answer as an unknown id. The pending confirmation token lives only here, on the
server: it is never put in a reply, a model prompt or an API response.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

Stage = Literal["collecting", "clarifying", "awaiting_recognition", "awaiting_confirmation", "resolved",
                "recognized", "handed_off", "abstained", "closed"]
FINAL_STAGES = frozenset({"resolved", "recognized", "handed_off", "abstained", "closed"})


@dataclass(frozen=True)
class Option:
    """One numbered choice shown to the customer: a charge or a card.

    `charge` holds the verified fields of a charge (evidence.charge_details) and `reasons` the match reasons that
    fired for it (evidence.match_reasons); both are tool data, never model text.
    """

    index: int
    kind: Literal["transaction", "card"]
    record_id: str
    label: str
    charge: dict[str, Any] | None = field(default=None, compare=False, hash=False)
    reasons: tuple[dict[str, Any], ...] = field(default=(), compare=False, hash=False)


@dataclass
class PendingConfirmation:
    confirmation_id: str
    tool: str
    args: dict[str, Any]
    expires_at: datetime
    label: str
    must_escalate: bool
    reason_code: str | None
    rule_ids: list[str]
    confidence: float | None
    token: str = field(repr=False, default="")
    charge: dict[str, Any] | None = None
    reasons: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RecognitionCheck:
    """The "do you recognize it?" step: the charge and its evidence, shown before any confirmation is issued.

    Holds what the confirmation will need if the customer does not recognize the charge (the narrowed policy
    decision and the identification confidence). No token exists yet: it is issued only after that answer.
    """

    recognition_id: str
    transaction_id: str
    label: str
    charge: dict[str, Any]
    reasons: list[dict[str, Any]]
    decision: Any  # PolicyDecision after narrow()
    confidence: float | None


@dataclass
class TrailStep:
    """One entry of the decision trail shown to the customer console and written to the audit log."""

    trace_id: str
    step: str
    outcome: str
    detail: dict[str, Any] = field(default_factory=dict)
    rule_ids: list[str] = field(default_factory=list)
    latency_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "step": self.step, "outcome": self.outcome, "detail": self.detail,
                "rule_ids": self.rule_ids, "latency_ms": round(self.latency_ms, 2)}


@dataclass
class Slots:
    """What the customer has told us so far. New values replace old ones; nothing is asked twice."""

    amount: float | None = None
    currency: str | None = None
    date: date | None = None
    date_tolerance_days: int = 1
    merchant: str | None = None

    def merge(self, intent: Any) -> None:
        for name in ("amount", "currency", "date", "merchant"):
            value = getattr(intent, name, None)
            if value is not None:
                setattr(self, name, value)
        if getattr(intent, "date", None) is not None:
            self.date_tolerance_days = intent.date_tolerance_days

    def overrides(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.amount is not None:
            out["amount"] = self.amount
        if self.currency:
            out["currency"] = self.currency
        if self.date is not None:
            out["date_hint"] = self.date
            out["date_tolerance_days"] = self.date_tolerance_days
        if self.merchant:
            out["merchant_hint"] = self.merchant
        return out


@dataclass
class ConversationState:
    conversation_id: str
    customer_id: str
    customer_ref: str
    language: str
    created_at: datetime
    stage: Stage = "collecting"
    statements: list[str] = field(default_factory=list)
    slots: Slots = field(default_factory=Slots)
    options: list[Option] = field(default_factory=list)
    clarify_rounds: int = 0
    pending: PendingConfirmation | None = None
    recognition: RecognitionCheck | None = None
    transaction_id: str | None = None
    card_id: str | None = None
    case_id: str | None = None
    confidence: float | None = None
    facts: list[tuple[str, str]] = field(default_factory=list)  # (fact, source)
    actions: list[tuple[str, str, str | None]] = field(default_factory=list)  # (action, status, record id)
    evidence: list[str] = field(default_factory=list)
    claim_window: dict[str, Any] | None = None  # {"rule_id", "deadline"} from get_dispute_policy
    reasons: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # transaction id -> match reasons shown
    cards: dict[str, dict[str, str]] | None = None  # product id -> card type and last 4, from the profile tool
    rule_ids: list[str] = field(default_factory=list)
    handoff: dict[str, Any] | None = None
    trace_ids: list[str] = field(default_factory=list)
    trail: list[TrailStep] = field(default_factory=list)
    transcript: list[tuple[str, str]] = field(default_factory=list)  # (role, text) for the customer UI only

    @property
    def text(self) -> str:
        return " ".join(self.statements)

    def add_fact(self, fact: str, source: str) -> None:
        if (fact, source) not in self.facts:
            self.facts.append((fact, source))

    def add_rules(self, rule_ids: list[str]) -> None:
        self.rule_ids.extend(r for r in rule_ids if r not in self.rule_ids)


class ConversationStore:
    """In-memory store. A production deployment needs a shared store with TTLs (see agent/README.md)."""

    def __init__(self) -> None:
        self._items: dict[str, ConversationState] = {}
        self._lock = threading.Lock()

    def create(self, customer_id: str, customer_ref: str, language: str, now: datetime) -> ConversationState:
        state = ConversationState("cv_" + secrets.token_hex(8), customer_id, customer_ref, language, now)
        with self._lock:
            self._items[state.conversation_id] = state
        return state

    def get(self, conversation_id: str, customer_id: str) -> ConversationState | None:
        with self._lock:
            state = self._items.get(conversation_id)
        return state if state is not None and state.customer_id == customer_id else None

    def get_any(self, conversation_id: str) -> ConversationState | None:
        """For the human-agent console, which has its own access control."""
        with self._lock:
            return self._items.get(conversation_id)

    def all(self) -> list[ConversationState]:
        with self._lock:
            return list(self._items.values())

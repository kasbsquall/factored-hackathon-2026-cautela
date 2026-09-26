"""Collect verified facts from the stores and run the policy engine on them.

Shared by the permission layer (to gate write actions) and by get_dispute_policy (to explain them), so the
explanation a customer sees and the enforcement are the same computation.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from agent.policy.engine import PolicyDecision, PolicyInput, ProductFacts, TransactionFacts, evaluate
from agent.tools.repository import CaseStore, WarehouseRepository


def effective_product_status(cases: CaseStore, product: dict) -> str | None:
    """Source status overlaid with sandbox blocks. The source row is never modified."""
    return "Blocked" if cases.active_block(product["product_id"]) else product.get("product_status")


def policy_decision(repo: WarehouseRepository, cases: CaseStore, customer_id: str, as_of: datetime, *,
                    transaction_id: str | None = None, product_id: str | None = None,
                    intent: str = "unrecognized_charge", security_flags: Iterable[str] = (),
                    customer_requested_human: bool = False) -> PolicyDecision:
    customer = repo.get_customer(customer_id, faulted=False) or {}
    tx_facts = None
    if transaction_id:
        tx = repo.policy_inputs(transaction_id)
        if tx and tx["customer_id"] == customer_id:
            tx_facts = TransactionFacts(**{k: (float(v) if k in {"amount", "amount_usd", "fraud_score"}
                                                and v is not None else v) for k, v in tx.items()})
    product_facts = None
    if product_id:
        product = repo.get_product(product_id, faulted=False)
        if product and product["customer_id"] == customer_id:
            product_facts = ProductFacts(product_id=product_id, product_type=product["product_type"],
                                         product_status=effective_product_status(cases, product))
    return evaluate(PolicyInput(
        intent=intent, customer_country=customer.get("country"), as_of=as_of, transaction=tx_facts,
        product=product_facts, security_flags=sorted(set(security_flags)),
        customer_requested_human=customer_requested_human,
    ))

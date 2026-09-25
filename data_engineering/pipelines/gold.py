"""Gold layer: placeholder, intentionally not built yet.

Gold tables serve the agent tools (customer profile, recent transactions, dispute policy inputs) and the analytics
and ML datasets. Their shape depends on the workflow decision, which waits on the exploratory analysis of
call_center_interactions.contact_reason and complaints.category in the organizer data (docs/architecture.md,
section 5). Building them now would mean guessing.

When built, every gold row must keep its lineage: source table, source key and the silver _run_id.
"""

from __future__ import annotations

GOLD_TABLES: list[str] = []


def build_gold(*_args, **_kwargs) -> None:
    raise NotImplementedError("gold tables wait on the workflow EDA; see this module's docstring")

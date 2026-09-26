"""The fresh test split, ``test_fresh``: a second held-out set that no one has inspected.

The original ``test`` split was used for error analysis (the parser's number words and slang and the merchant
features were written after reading its errors), so its numbers are optimistic. ``test_fresh`` is built with the
same generator and label rule, with these differences:

* another generation seed (``FreshConfig.seed``), so report dates, targets, hints and wording are new draws;
* customers from the ``test`` customer bucket of the original seed (never train or val) that the original build
  did not even load, so no customer, transaction or no-match source of train, val or test appears here;
* the same report-date window as ``test``: the organizer transactions end on 2026-06-18, so no later window
  exists, and the window stays after train and val;
* a new phrasing family F7 (``render_fresh.py``), written before any model was run on it, next to F1 to F6;
* a larger Portuguese share, so the language breakdown has more Portuguese cases.

The file is frozen by its sha256 in ``manifest.json`` under ``test_fresh``; ``ml.scenarios.build --verify``
rebuilds it and refuses a mismatch. ``data_version`` covers train, val and test only, so the fitted artifacts stay
valid and nothing is refitted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from ml.scenarios.build import (LABELS, BuildConfig, customers_to_load, label_sequence, make_case, portuguese_twin,
                                rng_for, split_of, stratum_quotas, _h)
from ml.scenarios.hints import DEBIT_TYPES
from ml.scenarios.render import FRESH_FAMILIES, HELDOUT_FAMILIES, SEEN_FAMILIES

SPLIT = "test_fresh"


@dataclass(frozen=True)
class FreshConfig:
    seed: int = 20261001  # generation seed; differs from BuildConfig.seed
    bucket_seed: int = BuildConfig.seed  # the original split hash: only its "test" bucket is eligible
    n_es: int = 900
    min_per_stratum: int = 30
    pt_share: float = 0.35
    fresh_family_share: float = 0.35  # F7
    heldout_share: float = 0.25  # F5 and F6; the rest is F1 to F4
    period: tuple = ("2026-01-01", "2026-06-17")
    label_mix: dict = field(default_factory=lambda: {"match": 0.6, "ambiguous": 0.2, "no_match": 0.2})

    def build_config(self) -> BuildConfig:
        """The generator settings of the original build, with this seed, window and Portuguese share."""
        return BuildConfig(seed=self.seed, pt_share=self.pt_share, periods={SPLIT: self.period},
                           n_es={SPLIT: self.n_es}, min_per_stratum={SPLIT: self.min_per_stratum})


def eligible_customers(customers: list[dict], fresh: FreshConfig, original: BuildConfig | None = None) -> list[dict]:
    """Test-bucket customers the original build never loaded, in this split's sampling order."""
    used = set(customers_to_load(customers, original or BuildConfig(seed=fresh.bucket_seed)))
    pool = [c for c in customers
            if split_of(c["customer_id"], fresh.bucket_seed) == "test" and c["customer_id"] not in used]
    return sorted(pool, key=lambda c: _h(fresh.seed, "order", c["customer_id"]))


def customers_to_load_fresh(eligible: list[dict], fresh: FreshConfig, depth: int = 4, margin: int = 40) -> list[str]:
    quotas = stratum_quotas(eligible, fresh.n_es, fresh.min_per_stratum)
    taken: dict[tuple, int] = {}
    ids = []
    for c in eligible:
        k = (c["country"], c["segment"])
        if taken.get(k, 0) < depth * quotas[k] + margin:
            taken[k] = taken.get(k, 0) + 1
            ids.append(c["customer_id"])
    return sorted(ids)


def family_for(customer_id: str, fresh: FreshConfig) -> str:
    frng = rng_for(fresh.seed, "family", customer_id)
    r = frng.random()
    if r < fresh.fresh_family_share:
        return FRESH_FAMILIES[0]
    if r < fresh.fresh_family_share + fresh.heldout_share:
        return frng.choice(HELDOUT_FAMILIES)
    return frng.choice(SEEN_FAMILIES)


def build_fresh(eligible: list[dict], tx_by_customer: dict[str, list[dict]],
                fresh: FreshConfig) -> tuple[list[dict], dict]:
    """Pure function: eligible customers and their transactions in, cases and build stats out."""
    cfg = fresh.build_config()
    order = lambda c: _h(fresh.seed, "order", c["customer_id"])  # noqa: E731 (input order never matters)
    loaded = sorted((c for c in eligible if c["customer_id"] in tx_by_customer), key=order)
    foreign = [t for c in loaded[:400] for t in tx_by_customer[c["customer_id"]] if t["transaction_type"] in DEBIT_TYPES]
    quotas = stratum_quotas(eligible, fresh.n_es, fresh.min_per_stratum)
    skipped = {lab: 0 for lab in LABELS}
    cases = []
    for stratum in sorted(quotas):
        seq = label_sequence(quotas[stratum], fresh.label_mix, rng_for(fresh.seed, "labels", SPLIT, *stratum))
        i = 0
        for cust in (c for c in loaded if (c["country"], c["segment"]) == stratum):
            if i >= len(seq):
                break
            family = family_for(cust["customer_id"], fresh)
            case = make_case(cust, tx_by_customer[cust["customer_id"]], SPLIT, seq[i], family, cfg, foreign)
            if case is None:
                skipped[seq[i]] += 1
                continue
            case["family_heldout"] = family not in SEEN_FAMILIES
            cases.append(case)
            i += 1
    cases.sort(key=lambda c: (c["report_date"], c["customer_ref"]))
    out = []
    for n, case in enumerate(cases):
        case = {"case_id": f"{SPLIT}-{n:05d}-es", "source_case_id": None, **case}
        out.append(case)
        if rng_for(fresh.seed, "ptsel", case["case_id"]).random() < fresh.pt_share:
            out.append(portuguese_twin(case, cfg))
    stats = {"skipped_customers": skipped, "quotas": {f"{k[0]}-{k[1]}": v for k, v in sorted(quotas.items())}}
    return out, stats


def payload(cases: list[dict]) -> tuple[str, str]:
    """Serialized file (same format as the other splits) and its sha256."""
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in cases)
    return text, hashlib.sha256(text.encode()).hexdigest()


def manifest_section(cases: list[dict], stats: dict, fresh: FreshConfig, n_eligible: int, n_loaded: int,
                     load_stats: dict) -> dict:
    _, sha = payload(cases)
    return {"file_sha256": sha, "fresh_version": sha[:16], "count": len(cases), "config": asdict(fresh),
            "customers_eligible": n_eligible, "customers_loaded": n_loaded, "load_stats": load_stats, **stats,
            "note": "Frozen before any model was evaluated on it; see ml/scenarios/fresh.py. Not part of "
                    "data_version, so the fitted artifacts stay valid."}


def build_from_source(con, loc, customers: list[dict], fresh: FreshConfig | None = None) -> tuple[list[dict], dict]:
    """Load the eligible customers' transactions from the organizer files and build the split."""
    from ml.scenarios.source import load_transactions

    fresh = fresh or FreshConfig()
    eligible = eligible_customers(customers, fresh)
    wanted = customers_to_load_fresh(eligible, fresh)
    tx, load_stats = load_transactions(con, loc, wanted)
    cases, stats = build_fresh(eligible, tx, fresh)
    return cases, manifest_section(cases, stats, fresh, len(eligible), len(wanted), load_stats)


def verify_problems(committed: dict, cases: list[dict]) -> list[str]:
    section = committed.get(SPLIT)
    if section is None:
        return [f"manifest.json has no {SPLIT} section"]
    _, sha = payload(cases)
    return [] if sha == section["file_sha256"] else [
        f"{SPLIT}.jsonl sha256 {sha[:12]} != committed {section['file_sha256'][:12]}"]


def write_file(out_dir, cases: list[dict]) -> None:
    (out_dir / f"{SPLIT}.jsonl").write_text(payload(cases)[0], encoding="utf-8", newline="\n")

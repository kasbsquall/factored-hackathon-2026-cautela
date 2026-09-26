"""Deterministic scenario builder for the "which charge?" ranking task.

Usage::

    uv run python -m ml.scenarios.build --verify   # rebuild, check the committed manifest
    uv run python -m ml.scenarios.build            # rebuild and rewrite the manifest
    uv run python -m ml.scenarios.build --verify --source s3://<bucket>/data

Both modes also build the fresh test split (ml/scenarios/fresh.py), hashed under ``test_fresh`` in the manifest.

Every random choice comes from an RNG seeded by (seed, customer, purpose), so the
output does not depend on iteration order and two runs with the same seed and
the same input files produce byte-identical files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ml.scenarios.hints import (DEBIT_TYPES, NEAR_MIN_CUES, TARGET_STATUSES, consistent_ids, corrupt_cue,
                                near_consistent_ids, sample_hints)
from ml.scenarios.render import HELDOUT_FAMILIES, SEEN_FAMILIES, render
from ml.scenarios.vocab import MERCHANTS

LABELS = ("match", "ambiguous", "no_match")
SPLITS = ("train", "val", "test")
CANDIDATE_FIELDS = ("transaction_id", "ts", "amount", "currency", "transaction_type", "channel", "merchant_name",
                    "merchant_category", "transaction_city", "transaction_country", "transaction_status")


@dataclass(frozen=True)
class BuildConfig:
    seed: int = 20260925
    window_days: int = 90
    min_pool: int = 3
    max_target_age: int = 60
    n_es: dict = field(default_factory=lambda: {"train": 1800, "val": 600, "test": 900})
    label_mix: dict = field(default_factory=lambda: {"match": 0.6, "ambiguous": 0.2, "no_match": 0.2})
    pt_share: float = 0.25
    heldout_share_test: float = 0.4
    recall_error_rate: float = 0.35  # share of eligible match cases (3+ cues) given one misremembered cue
    min_per_stratum: dict = field(default_factory=lambda: {"train": 40, "val": 20, "test": 30})
    periods: dict = field(default_factory=lambda: {"train": ("2023-10-01", "2025-06-30"),
                                                   "val": ("2025-07-01", "2025-12-31"),
                                                   "test": ("2026-01-01", "2026-06-17")})


def _h(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:15], 16)


def rng_for(*parts: object) -> random.Random:
    return random.Random(_h(*parts))


def split_of(customer_id: str, seed: int) -> str:
    """Group split by customer: 60 / 20 / 20 by hash."""
    b = _h(seed, "split", customer_id) % 10
    return "train" if b < 6 else ("val" if b < 8 else "test")


def customer_ref(customer_id: str) -> str:
    return "cust_" + hashlib.sha256(customer_id.encode()).hexdigest()[:12]


def stratum_quotas(customers: list[dict], n: int, floor: int) -> dict[tuple, int]:
    counts: dict[tuple, int] = {}
    for c in customers:
        counts[(c["country"], c["segment"])] = counts.get((c["country"], c["segment"]), 0) + 1
    total = sum(counts.values())
    raw = {k: max(floor, round(n * v / total)) for k, v in counts.items()}
    excess = sum(raw.values()) - n
    for k in sorted(raw, key=lambda k: -raw[k]):  # take the floor's surplus from the largest strata
        if excess <= 0:
            break
        take = min(excess, raw[k] - floor)
        raw[k] -= take
        excess -= take
    return raw


def label_sequence(quota: int, mix: dict, rng: random.Random) -> list[str]:
    seq: list[str] = []
    for lab in LABELS:
        seq += [lab] * round(quota * mix[lab])
    while len(seq) < quota:
        seq.append("match")
    seq = seq[:quota]
    rng.shuffle(seq)
    return seq


# ------------------------------------------------------------------ generation

def _age(report: date, tx: dict) -> int:
    return (report - tx["date"]).days


def _eligible_targets(pool: list[dict], report: date, max_age: int) -> list[dict]:
    return [t for t in pool if t["transaction_type"] in DEBIT_TYPES and t["transaction_status"] in TARGET_STATUSES
            and _age(report, t) <= max_age]


def _pick_target(targets: list[dict], report: date, rng: random.Random) -> dict:
    weights = [1.0 / (1.0 + _age(report, t) / 10.0) for t in targets]
    return rng.choices(targets, weights=weights, k=1)[0]


def gen_match(pool, report, family, rng, cfg):
    targets = _eligible_targets(pool, report, cfg.max_target_age)
    for _ in range(12 if targets else 0):
        t = _pick_target(targets, report, rng)
        level = rng.choices(["rich", "medium", "sparse"], weights=[0.45, 0.4, 0.15])[0]
        hints = sample_hints(rng, t, report, level, family)
        if not hints or consistent_ids(hints, pool) != [t["transaction_id"]]:
            continue
        found = {"target": t, "hints": hints, "consistent": [t["transaction_id"]], "level": level}
        wrongable = [c for c in ("amount", "date", "channel") if c in hints]
        if len(hints) >= NEAR_MIN_CUES and wrongable and rng.random() < cfg.recall_error_rate:
            cue = rng.choice(wrongable)
            bad = corrupt_cue(rng, hints, cue, t, report, family)
            if not consistent_ids(bad, pool) and near_consistent_ids(bad, pool) == [t["transaction_id"]]:
                found.update({"hints": bad, "consistent": [], "recall_error": cue})
        return found
    return None


def gen_ambiguous(pool, report, family, rng, cfg):
    targets = _eligible_targets(pool, report, cfg.max_target_age)
    for _ in range(20 if targets else 0):
        t = _pick_target(targets, report, rng)
        level = rng.choice(["medium", "sparse"])
        hints = sample_hints(rng, t, report, level, family)
        cons = consistent_ids(hints, pool) if hints else []
        if len(cons) >= 2 and t["transaction_id"] in cons:
            others = [c for c in pool if c["transaction_id"] in cons and c is not t]
            twin = any(o["transaction_type"] == t["transaction_type"] and o.get("merchant_name") == t.get("merchant_name")
                       and abs(o["amount"] / t["amount"] - 1) <= 0.25 for o in others if t["amount"] > 0)
            return {"target": t, "hints": hints, "consistent": cons, "level": level,
                    "ambiguity_kind": "twin" if twin else "underspecified"}
    return None


def gen_no_match(pool, report, family, rng, cfg, foreign):
    debits = [t for t in pool if t["transaction_type"] in DEBIT_TYPES]
    pool_merchants = {t.get("merchant_name") for t in pool}
    for _ in range(20):
        kind = "perturbed" if (debits and rng.random() < 0.6) else "foreign"
        if kind == "perturbed":
            phantom = dict(rng.choice(debits))
            attrs = ["amount", "date"] + (["merchant"] if phantom.get("merchant_name") else [])
            forced = rng.choice(attrs)
            if forced == "amount":
                phantom["amount"] = round(phantom["amount"] * rng.choice([0.3, 0.45, 2.2, 3.5]), 2)
            elif forced == "merchant":
                phantom["merchant_name"] = rng.choice([m for m in MERCHANTS if m not in pool_merchants] or MERCHANTS)
            else:
                phantom["date"] = report - timedelta(days=rng.randint(0, cfg.max_target_age))
        else:
            same = [f for f in foreign if f["currency"] == (debits[0]["currency"] if debits else f["currency"])]
            if not same:
                continue
            phantom = dict(rng.choice(same))
            phantom["date"] = report - timedelta(days=rng.randint(0, 45))
            forced = None
        level = rng.choice(["rich", "medium"])
        hints = sample_hints(rng, phantom, report, level, family)
        if forced and forced not in hints:
            extra = sample_hints(rng, phantom, report, "rich", family)
            if forced in extra:
                hints[forced] = extra[forced]
        if len(hints) >= 2 and not consistent_ids(hints, pool) and not near_consistent_ids(hints, pool):
            return {"target": None, "hints": hints, "consistent": [], "level": level, "no_match_kind": kind}
    return None


def _candidate(t: dict) -> dict:
    out = {k: t.get(k) for k in CANDIDATE_FIELDS}
    out["transaction_date"] = out.pop("ts")
    return out


def make_case(cust: dict, txs: list[dict], split: str, label: str, family: str, cfg: BuildConfig,
              foreign: list[dict]) -> dict | None:
    rng = rng_for(cfg.seed, "case", cust["customer_id"], label, family)
    lo, hi = (date.fromisoformat(d) for d in cfg.periods[split])
    for _ in range(6):
        report = lo + timedelta(days=rng.randint(0, (hi - lo).days))
        pool = [t for t in txs if report - timedelta(days=cfg.window_days) < t["date"] <= report]
        if len(pool) < cfg.min_pool:
            continue
        if label == "match":
            g = gen_match(pool, report, family, rng, cfg)
        elif label == "ambiguous":
            g = gen_ambiguous(pool, report, family, rng, cfg)
        else:
            g = gen_no_match(pool, report, family, rng, cfg, foreign)
        if g is None:
            continue
        variant = {"MX": "es-MX", "CO": "es-CO", "AR": "es-AR"}[cust["country"]]
        text = render(g["hints"], "es", cust["country"], family, rng)
        pool_sorted = sorted(pool, key=lambda t: (t["ts"], t["transaction_id"]), reverse=True)
        return {
            "language": "es", "variant": variant, "split": split, "family": family,
            "family_heldout": family in HELDOUT_FAMILIES, "customer_ref": customer_ref(cust["customer_id"]),
            "country": cust["country"], "segment": cust["segment"], "report_date": report.isoformat(),
            "description": text, "label": label,
            "target_transaction_id": g["target"]["transaction_id"] if label != "no_match" else None,
            "consistent_ids": g["consistent"], "info_level": g["level"],
            "ambiguity_kind": g.get("ambiguity_kind"), "no_match_kind": g.get("no_match_kind"),
            "recall_error": g.get("recall_error"),
            "hints": _jsonable_hints(g["hints"]), "candidates": [_candidate(t) for t in pool_sorted],
            "text_origin": "team-generated template over real organizer transactions",
        }
    return None


def _jsonable_hints(hints: dict) -> dict:
    return json.loads(json.dumps(hints, default=str))


def portuguese_twin(case: dict, cfg: BuildConfig) -> dict:
    rng = rng_for(cfg.seed, "pt", case["case_id"])
    twin = dict(case)
    twin.update({"case_id": case["case_id"].replace("-es", "-pt"), "source_case_id": case["case_id"],
                 "language": "pt", "variant": "pt-BR",
                 "description": render(case["hints"], "pt", case["country"], case["family"], rng),
                 "text_origin": "TEAM-GENERATED pt-BR rendering of the same hints; not organizer text"})
    return twin


def customers_by_split(customers: list[dict], cfg: BuildConfig) -> dict[str, list[dict]]:
    """All customers per split, in the deterministic hash order used for sampling."""
    by_split: dict[str, list[dict]] = {s: [] for s in SPLITS}
    for c in sorted(customers, key=lambda c: _h(cfg.seed, "order", c["customer_id"])):
        by_split[split_of(c["customer_id"], cfg.seed)].append(c)
    return by_split


def customers_to_load(customers: list[dict], cfg: BuildConfig, depth: int = 4, margin: int = 40) -> list[str]:
    """First ``depth * quota + margin`` customers of each split and stratum, in sampling order."""
    ids = []
    for split, members in customers_by_split(customers, cfg).items():
        quotas = stratum_quotas(members, cfg.n_es[split], cfg.min_per_stratum[split])
        taken: dict[tuple, int] = {}
        for c in members:
            k = (c["country"], c["segment"])
            if taken.get(k, 0) < depth * quotas[k] + margin:
                taken[k] = taken.get(k, 0) + 1
                ids.append(c["customer_id"])
    return sorted(ids)


def build(customers: list[dict], tx_by_customer: dict[str, list[dict]], cfg: BuildConfig) -> tuple[dict, dict]:
    """Pure function: customers and transactions in, cases per split and build stats out."""
    by_split = customers_by_split(customers, cfg)
    cases: dict[str, list[dict]] = {s: [] for s in SPLITS}
    stats: dict = {"skipped_customers": {s: {lab: 0 for lab in LABELS} for s in SPLITS}}
    for split in SPLITS:
        pool_customers = by_split[split]
        loaded = [c for c in pool_customers if c["customer_id"] in tx_by_customer]
        foreign = [t for c in loaded[:400] for t in tx_by_customer[c["customer_id"]]
                   if t["transaction_type"] in DEBIT_TYPES]
        quotas = stratum_quotas(pool_customers, cfg.n_es[split], cfg.min_per_stratum[split])
        stats.setdefault("quotas", {})[split] = {f"{k[0]}-{k[1]}": v for k, v in sorted(quotas.items())}
        for stratum in sorted(quotas):
            members = [c for c in loaded if (c["country"], c["segment"]) == stratum]
            seq = label_sequence(quotas[stratum], cfg.label_mix, rng_for(cfg.seed, "labels", split, *stratum))
            i = 0
            for cust in members:
                if i >= len(seq):
                    break
                frng = rng_for(cfg.seed, "family", cust["customer_id"])
                heldout = split == "test" and frng.random() < cfg.heldout_share_test
                family = frng.choice(HELDOUT_FAMILIES if heldout else SEEN_FAMILIES)
                case = make_case(cust, tx_by_customer[cust["customer_id"]], split, seq[i], family, cfg, foreign)
                if case is None:
                    stats["skipped_customers"][split][seq[i]] += 1
                    continue
                cases[split].append(case)
                i += 1
    for split in SPLITS:
        cases[split].sort(key=lambda c: (c["report_date"], c["customer_ref"]))
        out = []
        for n, case in enumerate(cases[split]):
            case = {"case_id": f"{split}-{n:05d}-es", "source_case_id": None, **case}
            out.append(case)
            if rng_for(cfg.seed, "ptsel", case["case_id"]).random() < cfg.pt_share:
                out.append(portuguese_twin(case, cfg))
        cases[split] = out
    return cases, stats


def payloads(cases: dict) -> tuple[dict[str, str], dict[str, str], str]:
    """Serialized case files, their sha256 and the data version (hash of the three hashes)."""
    texts = {split: "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
             for split, rows in cases.items()}
    hashes = {split: hashlib.sha256(t.encode()).hexdigest() for split, t in texts.items()}
    return texts, hashes, hashlib.sha256("".join(hashes[s] for s in SPLITS).encode()).hexdigest()[:16]


def write_outputs(cases: dict, stats: dict, cfg: BuildConfig, out_dir: Path, extra: dict,
                  write_manifest: bool = True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    texts, hashes, data_version = payloads(cases)
    for split, text in texts.items():
        (out_dir / f"{split}.jsonl").write_text(text, encoding="utf-8", newline="\n")
    manifest = {"data_version": data_version, "file_sha256": hashes, "config": asdict(cfg),
                "counts": {s: len(r) for s, r in cases.items()}, **stats, **extra,
                "label_note": "Labels are valid by construction: see ml/DATASHEET.md."}
    if write_manifest:
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                               encoding="utf-8", newline="\n")
    return manifest


def verify_against(manifest_path: Path, cases: dict) -> list[str]:
    """Differences between freshly built cases and a committed manifest (empty list = reproduced)."""
    committed = json.loads(manifest_path.read_text(encoding="utf-8"))
    _, hashes, data_version = payloads(cases)
    problems = [f"{s}.jsonl sha256 {hashes[s][:12]} != committed {committed['file_sha256'][s][:12]}"
                for s in SPLITS if hashes[s] != committed["file_sha256"][s]]
    if data_version != committed["data_version"]:
        problems.append(f"data_version {data_version} != committed {committed['data_version']}")
    return problems


def main() -> None:
    from ml.scenarios import fresh
    from ml.scenarios.source import (default_source, load_customers, load_transactions, open_source,
                                     source_fingerprint)

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default=None, help="local folder or s3://<bucket>/data (default: data/raw if "
                                                   "present, else LATAM_BANK_S3_URI/data from .env)")
    ap.add_argument("--out", default="eval/cases/disputes", type=Path)
    ap.add_argument("--seed", default=BuildConfig.seed, type=int)
    ap.add_argument("--verify", action="store_true",
                    help="rebuild, compare with the committed manifest.json, write the case files only if they "
                         "match (the manifest is never rewritten); exit 1 otherwise")
    args = ap.parse_args()
    cfg = BuildConfig(seed=args.seed)
    con, loc = open_source(args.source or default_source())
    print(f"source: {loc.kind}")  # the bucket name is not printed
    customers = load_customers(con, loc)
    wanted_ids = customers_to_load(customers, cfg)
    tx, load_stats = load_transactions(con, loc, wanted_ids)
    cases, stats = build(customers, tx, cfg)
    fresh_cases, fresh_section = fresh.build_from_source(con, loc, customers)
    extra = {"source_fingerprint": source_fingerprint(con, loc), "load_stats": load_stats,
             "customers_loaded": len(wanted_ids), fresh.SPLIT: fresh_section}
    if args.verify:
        committed = json.loads((args.out / "manifest.json").read_text(encoding="utf-8"))
        problems = verify_against(args.out / "manifest.json", cases) + fresh.verify_problems(committed, fresh_cases)
        if problems:
            print("NOT REPRODUCED:\n  " + "\n  ".join(problems))
            raise SystemExit(1)
        write_outputs(cases, stats, cfg, args.out, extra, write_manifest=False)
        fresh.write_file(args.out, fresh_cases)
        print(f"reproduced: case files match the committed manifest ({payloads(cases)[2]}, "
              f"{fresh.SPLIT} {fresh_section['fresh_version']})")
        return
    manifest = write_outputs(cases, stats, cfg, args.out, extra)
    fresh.write_file(args.out, fresh_cases)
    print(json.dumps({"data_version": manifest["data_version"], "counts": manifest["counts"],
                      "skipped": stats["skipped_customers"]}, indent=2))


if __name__ == "__main__":
    main()

"""Structured hints and the consistency rule that defines the labels.

A description is generated from a set of structured hints (approximate amount,
relative date, merchant, type, channel, city). The label of a scenario is a
function of the hints and the candidate pool only:

* a candidate is *consistent* with the description when it is an approved or
  pending debit (a charge that reached the account) and
  satisfies every hint within the tolerances below;
* it is *near-consistent* when the description has three or more cues and the
  candidate fails exactly one of them (one misremembered detail);
* ``match``: exactly one consistent candidate, the target; or, for a
  "recall error" case, no consistent candidate and exactly one near-consistent
  candidate, the target;
* ``ambiguous``: two or more consistent candidates, the target among them;
* ``no_match``: no consistent and no near-consistent candidate.

Rankers never see the hints, only the rendered text and the candidates.
"""

from __future__ import annotations

import calendar
import json
import random
import unicodedata
from datetime import date, timedelta

from ml.scenarios.vocab import MERCHANT_NOUNS, MERCHANTS, NOUNS

AMOUNT_TOLERANCE = 1.25  # consistent if claimed/1.25 <= amount <= claimed*1.25
DATE_SLACK_DAYS = 2      # consistent if the date falls within the phrase range +- 2 days
NEAR_MIN_CUES = 3        # a single wrong cue is tolerated only when at least three cues were given
HELDOUT_DATE_KINDS = ("just_now", "other_week", "weekend", "formal_date")
DEBIT_TYPES = ("Purchase", "Withdrawal", "Transfer", "Payment", "Adjustment")
TARGET_STATUSES = ("Approved", "Pending")
MERCHANT_STOPWORDS = {"de", "el", "la", "tv"}


def norm(text: str) -> str:
    """Lowercase and strip accents."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# ---------------------------------------------------------------- dates

def most_recent_day(report: date, day: int) -> date | None:
    """Latest date on or before ``report`` whose day of month is ``day``."""
    year, month = report.year, report.month
    for _ in range(3):
        if day <= calendar.monthrange(year, month)[1]:
            candidate = date(year, month, day)
            if candidate <= report:
                return candidate
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return None


def date_options(report: date, age: int, heldout: bool) -> list[dict]:
    """Relative-date phrases whose day range covers ``age`` days before ``report``."""
    wd = report.weekday()
    prev_month_len = calendar.monthrange((report.replace(day=1) - timedelta(days=1)).year,
                                         (report.replace(day=1) - timedelta(days=1)).month)[1]
    target = report - timedelta(days=age)
    opts = [
        ("today", 0, 0, {}), ("yesterday", 1, 1, {}), ("day_before", 2, 2, {}),
        ("recently", 0, 14, {}), ("two_weeks", 10, 18, {}), ("about_month", 22, 40, {}),
        ("last_week", wd + 1, wd + 7, {}), ("this_week", 0, wd, {}),
        ("end_last_month", report.day, report.day + 9, {}),
        ("last_month", report.day, report.day + prev_month_len - 1, {}),
    ]
    for n in range(3, 7):
        opts.append(("days_ago", n - 1, n + 1, {"n": n}))
    if report.day > 10:
        opts.append(("early_month", report.day - 10, report.day - 1, {}))
    if 1 <= age <= 6:
        opts.append(("weekday", age, age, {"weekday": target.weekday()}))
    if most_recent_day(report, target.day) == target:
        opts.append(("exact_day", age, age, {"day": target.day, "month": target.month, "year": target.year}))
    if heldout:
        opts.append(("just_now", 0, 1, {}))
        opts.append(("other_week", wd + 1, wd + 7, {}))
        if target.weekday() >= 5 and age <= 8:
            sat_age = age + (target.weekday() - 5)
            opts.append(("weekend", sat_age - 1, sat_age, {}))
        if age <= 60:
            opts.append(("formal_date", age, age, {"day": target.day, "month": target.month, "year": target.year}))
    return [{"kind": k, "lo_age": lo, "hi_age": hi, **p} for k, lo, hi, p in opts if lo <= age <= hi]


def sample_date_hint(rng: random.Random, report: date, tx_date: date, family: str) -> dict | None:
    age = (report - tx_date).days
    heldout = family in ("F5", "F6")
    remembered = age
    if rng.random() < 0.1:  # mild misremembering, kept inside the consistency slack
        remembered = max(0, age + rng.choice([-2, -1, 1, 2]))
    opts = date_options(report, remembered, heldout)
    if heldout:
        own_kinds = ("formal_date",) if family == "F5" else ("just_now", "other_week", "weekend")
        opts = [o for o in opts if o["kind"] not in HELDOUT_DATE_KINDS or o["kind"] in own_kinds]
        own = [o for o in opts if o["kind"] in own_kinds]
        if own and rng.random() < 0.7:
            opts = own
    else:
        # prefer informative phrases; "recently" and "last_month" are kept but rarer
        vague = [o for o in opts if o["kind"] in ("recently", "last_month")]
        precise = [o for o in opts if o not in vague]
        if precise and rng.random() < 0.8:
            opts = precise
    if not opts:
        return None
    hint = dict(rng.choice(opts))
    hint["lo"] = (report - timedelta(days=hint["hi_age"])).isoformat()
    hint["hi"] = (report - timedelta(days=hint["lo_age"])).isoformat()
    return hint


# ---------------------------------------------------------------- amounts

def round_sig(x: float, sig: int) -> float:
    if x <= 0:
        return 0.0
    digits = sig - len(str(int(x)))
    return round(x, digits) if digits < 0 else round(x)


def sample_amount_hint(rng: random.Random, amount: float, currency: str) -> dict:
    stated_currency = currency if rng.random() < 0.7 else None
    if rng.random() < 0.15:
        return {"claimed": round(amount, 2), "exact": True, "currency": stated_currency}
    noisy = amount * rng.uniform(0.9, 1.1)
    claimed = round_sig(noisy, rng.choice([2, 2, 2, 3]))
    one_sig = round_sig(noisy, 1)
    if rng.random() < 0.3 and abs(one_sig / amount - 1) < 0.12:
        claimed = one_sig
    if not (amount / AMOUNT_TOLERANCE <= claimed <= amount * AMOUNT_TOLERANCE):
        claimed = round_sig(amount, 2)
    return {"claimed": float(claimed), "exact": False, "currency": stated_currency}


# ---------------------------------------------------------------- merchants

def merchant_tokens(merchant: str) -> list[str]:
    return [t for t in norm(merchant).split() if len(t) >= 4 and t not in MERCHANT_STOPWORDS]


def compatible_with_token(token: str) -> list[str]:
    return [m for m in MERCHANTS if token in norm(m).split()]


def typo(rng: random.Random, word: str) -> str:
    if len(word) < 4:
        return word
    i = rng.randrange(1, len(word) - 1)
    op = rng.choice(["swap", "drop", "double", "vowel"])
    if op == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if op == "drop":
        return word[:i] + word[i + 1:]
    if op == "double":
        return word[:i] + word[i] + word[i:]
    vowels = "aeiou"
    return "".join(rng.choice(vowels) if (j == i and c in vowels) else c for j, c in enumerate(word))


def sample_merchant_hint(rng: random.Random, merchant: str) -> dict:
    r = rng.random()
    tokens = merchant_tokens(merchant)
    if r < 0.35 or (r < 0.55 and not tokens):
        return {"form": "exact", "surface": merchant, "compatible": [merchant]}
    if r < 0.55:
        tok = rng.choice(tokens)
        surface = next(w for w in merchant.split() if norm(w) == tok)
        return {"form": "partial", "surface": surface, "compatible": compatible_with_token(tok)}
    if r < 0.75:
        words = merchant.split()
        j = max(range(len(words)), key=lambda k: len(words[k]))
        words[j] = typo(rng, words[j])
        surface = " ".join(words)
        if rng.random() < 0.5:
            surface = norm(surface)
        return {"form": "typo", "surface": surface, "compatible": [merchant]}
    key = rng.choice(MERCHANT_NOUNS[merchant])
    return {"form": "noun", "noun_key": key, "surface": key, "compatible": list(NOUNS[key]["merchants"])}


# ---------------------------------------------------------------- hint sets

LEVEL_SLOTS = {"rich": 3, "medium": 2, "sparse": 1}


def sample_hints(rng: random.Random, tx: dict, report: date, level: str, family: str) -> dict:
    """Sample a hint set describing ``tx`` at the given information level."""
    what = "merchant" if tx.get("merchant_name") else "type"
    core = ["amount", "date", what]
    if level == "sparse":
        chosen = [rng.choice(core + ["channel"])]
    else:
        chosen = rng.sample(core, LEVEL_SLOTS[level])
        if rng.random() < (0.5 if level == "rich" else 0.2):
            chosen.append("channel")
    if rng.random() < 0.08 and tx.get("transaction_city"):
        chosen.append("city")
    hints: dict = {}
    for slot in chosen:
        if slot == "amount":
            hints["amount"] = sample_amount_hint(rng, tx["amount"], tx["currency"])
        elif slot == "date":
            d = sample_date_hint(rng, report, tx["date"], family)
            if d:
                hints["date"] = d
        elif slot == "merchant":
            hints["merchant"] = sample_merchant_hint(rng, tx["merchant_name"])
        elif slot == "type":
            hints["type"] = {"value": tx["transaction_type"]}
        elif slot == "channel":
            hints["channel"] = {"value": tx["channel"]}
        elif slot == "city":
            hints["city"] = {"value": tx["transaction_city"]}
    return hints


def cue_checks(hints: dict, cand: dict) -> dict[str, bool]:
    """Per-cue agreement between a hint set and one candidate."""
    out: dict[str, bool] = {}
    if "amount" in hints:
        h = hints["amount"]
        ok = h["claimed"] / AMOUNT_TOLERANCE <= cand["amount"] <= h["claimed"] * AMOUNT_TOLERANCE
        out["amount"] = ok and not (h["currency"] and h["currency"] != cand["currency"])
    if "date" in hints:
        lo = date.fromisoformat(hints["date"]["lo"]) - timedelta(days=DATE_SLACK_DAYS)
        hi = date.fromisoformat(hints["date"]["hi"]) + timedelta(days=DATE_SLACK_DAYS)
        out["date"] = lo <= cand["date"] <= hi
    if "merchant" in hints:
        out["merchant"] = cand.get("merchant_name") in hints["merchant"]["compatible"]
    for key, col in (("type", "transaction_type"), ("channel", "channel"), ("city", "transaction_city")):
        if key in hints:
            out[key] = cand.get(col) == hints[key]["value"]
    return out


def _disputable(cand: dict) -> bool:
    """A charge that reached the account: a debit that was approved or is pending (not declined or reversed)."""
    return cand["transaction_type"] in DEBIT_TYPES and cand.get("transaction_status") in TARGET_STATUSES


def is_consistent(hints: dict, cand: dict) -> bool:
    """Satisfies every cue. Only a disputable charge can be the one the customer means."""
    return _disputable(cand) and all(cue_checks(hints, cand).values())


def is_near_consistent(hints: dict, cand: dict) -> bool:
    """Fails exactly one cue of a description with three or more cues (one misremembered detail)."""
    if not _disputable(cand) or len(hints) < NEAR_MIN_CUES:
        return False
    return list(cue_checks(hints, cand).values()).count(False) == 1


def consistent_ids(hints: dict, pool: list[dict]) -> list[str]:
    return [c["transaction_id"] for c in pool if is_consistent(hints, c)]


def near_consistent_ids(hints: dict, pool: list[dict]) -> list[str]:
    return [c["transaction_id"] for c in pool if is_near_consistent(hints, c)]


def corrupt_cue(rng: random.Random, hints: dict, cue: str, tx: dict, report: date, family: str) -> dict:
    """Return a copy of ``hints`` where ``cue`` is misremembered beyond tolerance."""
    out = json.loads(json.dumps(hints, default=str))
    if cue == "amount":
        factor = rng.choice([rng.uniform(1.35, 1.8), 1 / rng.uniform(1.35, 1.8)])
        out["amount"] = {**out["amount"], "claimed": float(round_sig(tx["amount"] * factor, 2)), "exact": False}
    elif cue == "date":
        shift = rng.choice([-1, 1]) * rng.randint(6, 12)
        wrong = min(report, tx["date"] + timedelta(days=shift))
        d = sample_date_hint(rng, report, wrong, family)
        if d is None:
            return hints
        out["date"] = d
    elif cue == "channel":
        others = [c for c in ("ATM", "App", "Web", "POS", "Branch") if c != tx["channel"]]
        out["channel"] = {"value": rng.choice(others)}
    return out

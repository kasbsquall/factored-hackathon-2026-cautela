"""Build the noisy-customer slice: dispute conversations whose customer misremembers, misspells or mistypes a detail.

    uv run python -m eval.noisy.build --split test              # dev half -> eval/noisy/dev/ (suite, gold, manifest)
    uv run python -m eval.noisy.build --split test --verify     # rebuild in memory and compare with the manifest
    uv run python -m eval.noisy.build --split test_fresh --allow-sealed   # sealed half, only after the code freeze

The held-out suite's simulated customer restates its description's hints exactly, so free-text understanding is
never tested against a customer who gets a detail wrong. Here each conversation carries one noise family:

  wrong_date          a date 2 to 6 days off the real one, or a vague relative date around the wrong day
  partial_merchant    the merchant misspelled, cut short like a statement descriptor, or named by what it sells
  approx_amount       the amount rounded and 10 to 20 percent off, usually with a hedge ("como 50 mil")
  self_correction     a wrong amount or date, then the true value in the same message or the next one
  chat_style          true values in whatsapp register: lowercase, no accents, abbreviations, typos, short dates
  wrong_restatement   a vague but true opener; asked for details, the customer adds one wrong detail

Noise lives only in the spec texts (turns and customer.restatement), so eval/harness.py and its compliant customer
are unchanged: the customer never recognizes the charge it is shown, accepts every confirmation and picks the charge
it means (customer.target, never altered) from numbered options. Every other detail a message carries is the true
value of that charge, so a failure can be traced to the family. Gold is the held-out suite's, from the same code:
eval/build.py's case_gold (the policy oracle on the charge the customer means) at the same clock start.

The split is a parameter because the same frozen generator builds the sealed half from test_fresh after the code
freeze; that split is refused unless allow_sealed=True (--allow-sealed). Seeded and order-independent: two builds
of a split give the same bytes. The manifest records the sha256 of suite, gold and generator source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from eval import build as heldout
from eval.noisy import texts
from eval.oracle import rules as oracle_rules
from eval.paths import (DISPUTES_DIR, EVAL_DIR, FRESH_SLICE_PATH, NOISY_DEV_DIR, NOISY_SEALED_DIR, ROOT,
                        SLICE_PATH)

SEED = 20260929
VERSION = "noisy-v1"
FAMILIES = ("wrong_date", "partial_merchant", "approx_amount", "self_correction", "chat_style", "wrong_restatement")
PER_FAMILY = {"es": 30, "pt": 10}  # 40 per family, 240 in all, 75% Spanish
SPLITS = {"test": (SLICE_PATH, NOISY_DEV_DIR), "test_fresh": (FRESH_SLICE_PATH, NOISY_SEALED_DIR)}
SEALED_SPLITS = frozenset({"test_fresh"})
CUE_ORDER = ("merchant", "amount", "date", "channel", "city")
GENERATOR_SOURCES = (Path(__file__), Path(texts.__file__), EVAL_DIR / "build.py", EVAL_DIR / "oracle.py")


class SealedSplitError(RuntimeError):
    """The sealed split was requested without allow_sealed: it may only be built after the code freeze."""


def check_split(split: str, allow_sealed: bool = False) -> tuple[Path, Path]:
    """(warehouse slice, output dir) for a split. Nothing is read here, so a refusal touches no sealed data."""
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}: one of {sorted(SPLITS)}")
    if split in SEALED_SPLITS and not allow_sealed:
        raise SealedSplitError(f"{split} is the sealed half: build it only after the code freeze, with "
                               "allow_sealed=True (--allow-sealed)")
    return SPLITS[split]


def _rng(*parts: object, seed: int = SEED) -> random.Random:
    return heldout._rng(*parts, seed=seed)


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# ---- values as the customer says them ---------------------------------------------------------------------
def target_of(case: dict) -> dict:
    return next(c for c in case["candidates"] if c["transaction_id"] == case["target_transaction_id"])


def sig(x: float, digits: int) -> float:
    return float(f"{x:.{digits}g}")


def amount_text(value: float, currency: str, lang: str, country: str) -> str:
    """The exact amount as it reads on a statement: cents for dollars, whole pesos with dot thousands."""
    word = texts.CURRENCY.get(currency, texts.CURRENCY["MXN"])[lang]
    if currency == "USD":
        num = f"{value:.2f}".removesuffix(".00")
        if country != "MX" or lang == "pt":
            num = num.replace(".", ",")
        return f"{num} {word}"
    return f"{round(value):,}".replace(",", ".") + f" {word}"


def round_text(value: float, currency: str, lang: str, with_currency: bool = True) -> str:
    """A round figure as people say it: "1,5 millones de pesos", "170 mil", "90 dólares"."""
    word = texts.CURRENCY.get(currency, texts.CURRENCY["MXN"])[lang]
    if value >= 1_000_000:
        m = value / 1_000_000
        unit = texts.MILLION[lang][0] if m == 1 else texts.MILLION[lang][1]
        return f"{m:g}".replace(".", ",") + f" {unit}" + (f" de {word}" if with_currency else "")
    if value >= 1000 and currency != "USD":
        return f"{value / 1000:g}".replace(".", ",") + " mil" + (f" {word}" if with_currency else "")
    num = f"{int(value)}" if value >= 1000 else f"{value:g}"
    return num + (f" {word}" if with_currency else "")


def off_by(true: float, lo: float, hi: float, rng: random.Random) -> tuple[float, float]:
    """A round figure lo..hi (fractions) away from the true amount, and its actual relative error."""
    factor = 1 + rng.choice((-1, 1)) * rng.uniform(lo, hi)
    value = sig(true * factor, 2)
    if not lo * 0.5 <= abs(value - true) / true <= hi * 1.5:
        value = sig(true * factor, 3)
    return value, round((value - true) / true, 4)


def date_text(d: date, lang: str) -> str:
    return texts.DATE_EXACT[lang].format(d=d.day, month=texts.MONTHS[lang][d.month - 1])


def shifted(true: date, report: date, rng: random.Random) -> tuple[date, int]:
    """A date 2 to 6 days from the true one, never after the report date."""
    k = rng.randint(2, 6)
    step = rng.choice((-1, 1))
    if step == 1 and (report - (true + timedelta(days=k))).days < 1:
        step = -1
    return true + timedelta(days=step * k), step * k


def wrong_date_text(true: date, report: date, lang: str, rng: random.Random) -> tuple[str, dict]:
    when, shift = shifted(true, report, rng)
    age = (report - when).days
    form = rng.choice(("explicit", "explicit", "relative_days", "weekday"))
    if form == "weekday" and not 2 <= age <= 7:
        form = "relative_days"
    if form == "relative_days" and age < 2:
        form = "explicit"
    if form == "relative_days":
        text = rng.choice(texts.DATE_RELATIVE_DAYS[lang]).format(n=age)
    elif form == "weekday":
        wd = texts.WEEKDAYS[lang][when.weekday()]
        text = texts.DATE_WEEKDAY["es"].format(weekday=wd) if lang == "es" else texts.DATE_WEEKDAY["pt"][wd]
    else:
        text = date_text(when, lang)
    return text, {"stated_date": when.isoformat(), "shift_days": shift, "form": form}


def partial_merchant_text(name: str, lang: str, rng: random.Random) -> tuple[str, str]:
    form = rng.choice(("misspelled", "truncated", "generic"))
    if form == "misspelled":
        return texts.AT[lang] + texts.MERCHANT_MISSPELLED[name], form
    if form == "truncated":
        compact = re.sub(r"[^A-Z]", "", _plain(name).upper())
        cut = compact[:max(3, min(rng.choice((6, 7, 8)), len(compact) - 2))]
        return rng.choice(texts.MERCHANT_TRUNCATED_FRAME[lang]).format(x=cut), form
    generic = texts.MERCHANT_GENERIC[name][0 if lang == "es" else 1]
    return rng.choice(texts.MERCHANT_GENERIC_FRAME[lang]).format(x=generic), form


class Charge:
    """The charge the customer means, with its details rendered the clean way."""

    def __init__(self, case: dict) -> None:
        tx = target_of(case)
        self.case, self.tx, self.lang, self.country = case, tx, case["language"], case["country"]
        self.date = datetime.fromisoformat(tx["transaction_date"]).date()
        self.report = date.fromisoformat(case["report_date"])
        self.merchant = tx.get("merchant_name")
        hinted = set(case["hints"])
        self.cues = {k for k in ("amount", "date", "merchant", "type", "channel", "city") if k in hinted}
        if not self.merchant or self.merchant not in texts.MERCHANT_MISSPELLED:
            self.cues.discard("merchant")
        if tx.get("channel") not in texts.CHANNEL[self.lang]:
            self.cues.discard("channel")
        if not tx.get("transaction_city"):
            self.cues.discard("city")

    def amount(self) -> str:
        return amount_text(self.tx["amount"], self.tx["currency"], self.lang, self.country)

    def parts(self, extra: tuple[str, ...] = ()) -> dict[str, str]:
        lang, cues = self.lang, self.cues | set(extra)
        out = {}
        if "merchant" in cues:
            out["merchant"] = texts.AT[lang] + self.merchant
        if "amount" in cues:
            out["amount"] = texts.AMOUNT_OF[lang] + self.amount()
        if "date" in cues:
            out["date"] = date_text(self.date, lang)
        if "channel" in cues:
            out["channel"] = texts.CHANNEL[lang][self.tx["channel"]]
        if "city" in cues:
            out["city"] = texts.AT[lang] + self.tx["transaction_city"]
        return out

    def what(self) -> str:
        kind = self.tx.get("transaction_type") if "type" in self.cues else None
        return texts.WHAT[self.lang].get(kind, texts.WHAT[self.lang][None])

    def say(self, templates: dict, parts: dict[str, str], rng: random.Random) -> str:
        cues = "".join(parts[k] for k in CUE_ORDER if k in parts)
        return rng.choice(templates[self.lang]).format(what=self.what(), cues=cues)


# ---- families ------------------------------------------------------------------------------------------------
# Each returns (turns, restatement, altered). `altered` lists every detail that differs from the true charge (or,
# for chat_style, every change of register), with where it appears.
def wrong_date(ch: Charge, rng: random.Random):
    parts = ch.parts(("date",))
    parts["date"], info = wrong_date_text(ch.date, ch.report, ch.lang, rng)
    altered = [{"field": "date", "where": ["turn1", "restatement"], "true": ch.date.isoformat(),
                "stated": parts["date"].strip(), **info}]
    return [ch.say(texts.OPENERS, parts, rng)], ch.say(texts.RESTATE, parts, rng), altered


def partial_merchant(ch: Charge, rng: random.Random):
    parts = ch.parts(("merchant",))
    parts["merchant"], form = partial_merchant_text(ch.merchant, ch.lang, rng)
    altered = [{"field": "merchant", "where": ["turn1", "restatement"], "true": ch.merchant,
                "stated": parts["merchant"].strip(), "form": form}]
    return [ch.say(texts.OPENERS, parts, rng)], ch.say(texts.RESTATE, parts, rng), altered


def approx_amount(ch: Charge, rng: random.Random):
    parts = ch.parts(("amount",))
    value, err = off_by(ch.tx["amount"], 0.10, 0.20, rng)
    bare = value >= 1000 and ch.tx["currency"] != "USD" and rng.random() < 0.3  # "como 50 mil", no currency word
    said = rng.choice(texts.APPROX[ch.lang]).format(x=round_text(value, ch.tx["currency"], ch.lang, not bare))
    parts["amount"] = texts.AMOUNT_OF[ch.lang] + said
    altered = [{"field": "amount", "where": ["turn1", "restatement"], "true": ch.amount(), "stated": said,
                "stated_value": value, "relative_error": err, "currency_word": not bare}]
    return [ch.say(texts.OPENERS, parts, rng)], ch.say(texts.RESTATE, parts, rng), altered


def self_correction(ch: Charge, rng: random.Random):
    field = rng.choice(("amount", "date"))
    mode = rng.choice(("same_message", "next_message"))
    parts = ch.parts((field,))
    true_part = parts[field]
    if field == "amount":
        value, err = off_by(ch.tx["amount"], 0.25, 0.60, rng)
        wrong = round_text(value, ch.tx["currency"], ch.lang)
        wrong_part, info = texts.AMOUNT_OF[ch.lang] + wrong, {"stated_value": value, "relative_error": err}
        true_said = ch.amount()
    else:
        when, shift = shifted(ch.date, ch.report, rng)
        wrong_part, info = date_text(when, ch.lang), {"stated_date": when.isoformat(), "shift_days": shift}
        wrong, true_said = wrong_part.strip(), true_part
    if mode == "same_message":
        parts[field] = wrong_part + rng.choice(texts.CORRECT_INLINE[ch.lang]) + true_part
        turns = [ch.say(texts.OPENERS, parts, rng)]
    else:
        parts[field] = wrong_part
        fix = rng.choice(texts.CORRECT_NEXT[ch.lang][field]).format(true=true_said)
        turns = [ch.say(texts.OPENERS, parts, rng), fix]
    altered = [{"field": field, "where": ["turn1"], "mode": mode, "true": true_part.strip(), "stated": wrong,
                "corrected_to": true_part.strip(), **info}]
    return turns, ch.say(texts.RESTATE, ch.parts((field,)), rng), altered


def chat_style(ch: Charge, rng: random.Random):
    lang, ops = ch.lang, []
    parts = ch.parts()
    if "date" in parts:
        d, m = ch.date.day, ch.date.month
        form = rng.choice(("short_month", "slash", "full"))
        parts["date"] = {"short_month": f" el {d} de {texts.MONTHS_SHORT[lang][m - 1]}" if lang == "es"
                         else f" dia {d} de {texts.MONTHS_SHORT[lang][m - 1]}",
                         "slash": f" el {d}/{m}" if lang == "es" else f" dia {d}/{m}",
                         "full": date_text(ch.date, lang)}[form]
        ops.append(f"date:{form}")
    if "amount" in parts:
        value, cur = ch.tx["amount"], ch.tx["currency"]
        if cur == "USD":
            num = f"{value:.2f}".removesuffix(".00")
            form = rng.choice(("usd", "dls", "dollar_sign"))
            said = {"usd": f"{num} usd", "dls": f"{num} dls", "dollar_sign": f"${num}"}[form]
        else:
            form = rng.choice(("no_separator", "dollar_sign"))
            said = {"no_separator": f"{round(value)} pesos",
                    "dollar_sign": "$" + f"{round(value):,}".replace(",", ".")}[form]
        parts["amount"] = texts.AMOUNT_OF[lang] + said
        ops.append(f"amount:{form}")
    protected = set(re.findall(r"[a-z0-9]+", _plain(" ".join([ch.what(), *parts.values()]).lower())))
    turn, restated = ch.say(texts.OPENERS, parts, rng), ch.say(texts.RESTATE, parts, rng)
    turn, t_ops = chatify(turn, lang, rng, protected)
    restated, r_ops = chatify(restated, lang, rng, protected)
    altered = [{"field": "register", "where": ["turn1", "restatement"], "values_unchanged": True,
                "ops": ops + [f"turn1:{o}" for o in t_ops] + [f"restatement:{o}" for o in r_ops]}]
    return [turn], restated, altered


def chatify(text: str, lang: str, rng: random.Random, protected: set[str]) -> tuple[str, list[str]]:
    """Whatsapp register: lowercase, no accents or opening marks, abbreviations, one or two typos, no final stop.
    Words that carry the charge's values (protected) are never misspelled."""
    ops = ["lowercase", "no_accents"]
    out = _plain(text.lower()).replace("¿", "").replace("¡", "").rstrip(".")
    for full, short in texts.CHAT_ABBREV[lang]:
        pattern = rf"\b{re.escape(_plain(full))}\b"
        if re.search(pattern, out) and rng.random() < 0.7:
            out = re.sub(pattern, short, out)
            ops.append(f"abbrev:{_plain(full)}")
    words = out.split(" ")
    typo_at = [i for i, w in enumerate(words) if len(w) >= 5 and w.isalpha() and w not in protected]
    for i in sorted(rng.sample(typo_at, min(len(typo_at), rng.choice((1, 2))))):
        w = words[i]
        j = rng.randrange(1, len(w) - 2)
        words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:]
        ops.append(f"typo:{w}->{words[i]}")
    out = " ".join(words)
    lead = rng.choice(texts.CHAT_OPENERS[lang])
    if lead and not out.startswith(("hola", "ola", "buenas", "oi", "boa")):
        out = lead + out
        ops.append("chat_opener")
    return out, ops


def wrong_restatement(ch: Charge, rng: random.Random, pool: list[dict]):
    field = rng.choices(("date", "amount", "merchant"), weights=(4, 4, 2))[0]
    parts = ch.parts()
    if field == "date":
        text, info = wrong_date_text(ch.date, ch.report, ch.lang, rng)
        parts["date"] = text
        stated, true = text.strip(), ch.date.isoformat()
    elif field == "amount":
        value, err = off_by(ch.tx["amount"], 0.25, 0.50, rng)
        stated = round_text(value, ch.tx["currency"], ch.lang)
        parts["amount"], info, true = texts.AMOUNT_OF[ch.lang] + stated, {"stated_value": value,
                                                                           "relative_error": err}, ch.amount()
    else:
        others = sorted({c["merchant_name"] for c in pool if c.get("merchant_name")} - {ch.merchant})
        source = "own_pool" if others else "directory"
        others = others or sorted(set(texts.MERCHANT_MISSPELLED) - {ch.merchant})
        stated = rng.choice(others)
        parts["merchant"], info, true = texts.AT[ch.lang] + stated, {"source": source}, ch.merchant
    altered = [{"field": field, "where": ["restatement"], "true": true, "stated": stated,
                "added": field not in ch.cues, **info}]
    return [rng.choice(texts.VAGUE_OPENERS[ch.lang])], ch.say(texts.RESTATE, parts, rng), altered


def noisy_texts(family: str, case: dict, seed: int = SEED) -> tuple[list[str], str, list[dict]]:
    ch, rng = Charge(case), _rng("noise", family, case["case_id"], seed=seed)
    if family == "wrong_restatement":
        return wrong_restatement(ch, rng, case["candidates"])
    return {"wrong_date": wrong_date, "partial_merchant": partial_merchant, "approx_amount": approx_amount,
            "self_correction": self_correction, "chat_style": chat_style}[family](ch, rng)


# ---- sampling ------------------------------------------------------------------------------------------------
def fits(family: str, case: dict) -> bool:
    if family == "partial_merchant":
        return target_of(case).get("merchant_name") in texts.MERCHANT_MISSPELLED
    return True


def eligible(world: dict) -> list[dict]:
    """Dispute cases whose customer can log in, whose charge is known (match or ambiguous) and whose labelled pool
    is the pool the agent will see (pool parity). no_match cases say nothing about understanding a noisy detail."""
    customers = world["customers"]
    out = []
    for c in world["cases"]:
        who = customers[c["customer_ref"]]
        if who["status"] != "Active" or not who["contact"] or c["label"] not in ("match", "ambiguous"):
            continue
        if heldout.pool_parity(c, world, heldout.clock_start(c)):
            out.append(c)
    return sorted(out, key=lambda c: c["case_id"])


def assign(pool: list[dict], per_family: dict[str, int], seed: int = SEED) -> dict[str, list[dict]]:
    """Seeded draw, one conversation per source group (a Spanish case and its Portuguese rendering never both), the
    most constrained family first."""
    order = list(pool)
    _rng("assign", seed=seed).shuffle(order)
    used: set[str] = set()
    picked: dict[str, list[dict]] = {}
    for family in sorted(FAMILIES, key=lambda f: (f != "partial_merchant", FAMILIES.index(f))):
        picked[family] = []
        for lang, need in per_family.items():
            got = [c for c in order if c["language"] == lang and fits(family, c)
                   and (c.get("source_case_id") or c["case_id"]) not in used][:need]
            if len(got) < need:
                raise RuntimeError(f"{family}/{lang}: {len(got)} eligible cases, {need} needed")
            used |= {c.get("source_case_id") or c["case_id"] for c in got}
            picked[family] += got
    return picked


def build_suite(world: dict, split: str, seed: int = SEED, per_family: dict[str, int] | None = None) -> list[dict]:
    picked = assign(eligible(world), per_family or PER_FAMILY, seed)
    suite = []
    for family in FAMILIES:
        for case in picked[family]:
            turns, restated, altered = noisy_texts(family, case, seed)
            gold = heldout.case_gold(case, world, heldout.clock_start(case))
            conv = heldout.base_conv(case, world, f"nz-{family}-{case['case_id']}", "dispute", family, turns, gold,
                                     seed=seed, tags=[f"noise:{family}", f"family:{case['family']}"],
                                     noise={"family": family, "altered": altered}, split=split)
            conv["customer"]["restatement"] = restated
            conv["variance_subset"] = False
            suite.append(conv)
    return sorted(suite, key=lambda c: c["conv_id"])


# ---- outputs ------------------------------------------------------------------------------------------------
def serialize(rows: list[dict]) -> str:
    return heldout.serialize(rows)


def gold_rows(suite: list[dict]) -> list[dict]:
    """Gold per conversation with no organizer value: the charge the customer means only as a salted digest."""
    return [{"conv_id": c["conv_id"], "noise_family": c["noise"]["family"], "language": c["language"],
             "kind": c["gold"]["kind"], "reasons": c["gold"]["reasons"], "expects_case": c["gold"]["expects_case"],
             "label": c["gold"]["label"], "rule_basis": c["gold"]["rule_basis"],
             "target_sha256": hashlib.sha256(f"{VERSION}|{c['gold']['target']}".encode()).hexdigest()[:16]}
            for c in suite]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def generator_sha256() -> dict[str, str]:
    files = {p.relative_to(ROOT).as_posix(): sha256_file(p) for p in GENERATOR_SOURCES}
    return files | {"combined": sha256_text("".join(f"{k}:{v}\n" for k, v in sorted(files.items())))}


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def manifest_for(split: str, suite_text: str, gold_text: str, world: dict, suite: list[dict],
                 cases_path: Path, seed: int = SEED) -> dict:
    count = lambda key: dict(sorted(Counter(key(c) for c in suite).items()))  # noqa: E731
    return {
        "version": VERSION, "seed": seed, "split": split, "role": "sealed" if split in SEALED_SPLITS else "dev",
        "suite_sha256": sha256_text(suite_text), "gold_sha256": sha256_text(gold_text),
        "generator_sha256": generator_sha256(), "policy_version": oracle_rules()["version"],
        "warehouse_slice_content_hash": world["slice_hash"],
        "sources": {"cases_file": cases_path.relative_to(ROOT).as_posix(), "cases_sha256": sha256_file(cases_path)},
        "built_on_commit": _commit(),
        "conversations": len(suite),
        "by_family": count(lambda c: c["noise"]["family"]),
        "by_language": count(lambda c: c["language"]),
        "by_family_language": count(lambda c: f"{c['noise']['family']}/{c['language']}"),
        "by_gold": count(lambda c: c["gold"]["kind"] + (":" + "|".join(c["gold"]["reasons"])
                                                        if c["gold"]["reasons"] else "")),
        "by_label": count(lambda c: c["gold"]["label"]),
        "note": "Written before any configuration runs. suite.jsonl is git-ignored (organizer-derived ids and "
                "values); rebuild it with `uv run python -m eval.noisy.build --split " + split + " --verify`.",
    }


def build(split: str, allow_sealed: bool = False, seed: int = SEED) -> tuple[list[dict], str, str, dict]:
    slice_path, _ = check_split(split, allow_sealed)
    cases_path = DISPUTES_DIR / f"{split}.jsonl"
    world = heldout.load_world(cases_path, slice_path)
    suite = build_suite(world, split, seed)
    suite_text, gold_text = serialize(suite), serialize(gold_rows(suite))
    return suite, suite_text, gold_text, manifest_for(split, suite_text, gold_text, world, suite, cases_path, seed)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True, choices=sorted(SPLITS))
    ap.add_argument("--verify", action="store_true", help="rebuild and compare with the committed manifest")
    ap.add_argument("--allow-sealed", action="store_true", help="only after the code freeze: build test_fresh")
    args = ap.parse_args()
    try:
        _, out_dir = check_split(args.split, args.allow_sealed)
    except SealedSplitError as exc:
        raise SystemExit(str(exc)) from None
    manifest_path = out_dir / "manifest.json"
    if not args.verify and (out_dir / "results.json").exists():
        raise SystemExit(f"{out_dir}/results.json exists: the slice already ran; use --verify, nothing written")
    suite, suite_text, gold_text, manifest = build(args.split, args.allow_sealed)
    if args.verify:
        committed = json.loads(manifest_path.read_text(encoding="utf-8"))
        bad = [k for k in ("suite_sha256", "gold_sha256", "warehouse_slice_content_hash")
               if committed.get(k) != manifest[k]]
        if bad:
            raise SystemExit(f"mismatch with the committed manifest: {bad}; nothing written")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "suite.jsonl").write_text(suite_text, encoding="utf-8", newline="\n")
    if not args.verify:
        (out_dir / "gold.jsonl").write_text(gold_text, encoding="utf-8", newline="\n")
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: manifest[k] for k in ("split", "conversations", "by_family", "by_language",
                                                "suite_sha256", "gold_sha256")}, indent=2))


if __name__ == "__main__":
    main()

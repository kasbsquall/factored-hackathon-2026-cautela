"""The noisy-customer slice generator (eval/noisy/build.py) on a hand-built world: determinism, what each noise
family may and may not say, the charge the customer means, the split parameter and the sealed-split guard."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from string import Formatter

import pytest

from eval.noisy import build as noisy
from eval.noisy import texts, texts_sealed
from eval.paths import DISPUTES_DIR, FRESH_SLICE_PATH, NOISY_DEV_DIR, NOISY_SEALED_DIR, SLICE_PATH
from eval.slice import customer_ref

PER_FAMILY = {"es": 2, "pt": 1}
MERCHANTS = ("Super Ahorro", "Uber", "Cable TV", "Farmacia Salud", "Óptica Visión", "Boutique Moda")
HINT_SETS = (("amount", "date", "type"), ("type",), ("amount", "merchant"), ("date", "channel"), ("channel",),
             ("amount", "channel", "date", "type", "city"))


def _world(n: int = 40) -> dict:
    """n customers, each with four own charges in the last 90 days and one case in Spanish plus its Portuguese
    rendering. Every other case's charge has a merchant, so partial_merchant has enough to draw from."""
    cases, customers, facts, by_customer = [], {}, {}, {}
    for i in range(n):
        cid = f"CLI-T{i:05d}"
        ref = customer_ref(cid)
        customers[ref] = {"customer_id": cid, "status": "Active", "contact": True}
        report = date(2026, 3, 1) + timedelta(days=i)
        candidates = []
        for j in range(4):
            when = datetime.combine(report, datetime.min.time()) - timedelta(days=3 + 9 * j, hours=-9)
            usd = i % 3 == 0
            tx = {"transaction_id": f"TRX-T{i:04d}{j}AAAAA", "transaction_date": when.isoformat(sep=" "),
                  "amount": round(50 + 37.5 * j + i, 2) if usd else float(40_000 + 12_345 * j + 1000 * i),
                  "currency": "USD" if usd else "COP", "merchant_name": MERCHANTS[(i + j) % 6] if (i + j) % 2 == 0
                  else None, "transaction_type": ("Purchase", "Withdrawal", "Transfer", "Payment")[j],
                  "channel": ("POS", "ATM", "App", "Web")[(i + j) % 4], "transaction_city": "Bogotá",
                  "transaction_status": "Approved"}
            candidates.append(tx)
            facts[tx["transaction_id"]] = {
                "transaction_id": tx["transaction_id"], "customer_id": cid, "transaction_date": when,
                "amount": tx["amount"], "currency": tx["currency"], "amount_usd": tx["amount"] if usd else 12.0,
                "transaction_status": "Approved", "customer_country": "Colombia", "product_type": "Credit Card",
                "channel": tx["channel"], "fraud_score": 1.0, "is_fraud": False}
            by_customer.setdefault(cid, []).append(facts[tx["transaction_id"]])
        target = candidates[0] if i % 2 == 0 else candidates[1 + i % 3]
        day = target["transaction_date"][:10]
        full = {"amount": {"claimed": target["amount"], "currency": target["currency"], "exact": True},
                "date": {"lo": day, "hi": day}, "type": {"value": target["transaction_type"]},
                "merchant": {"form": "exact", "surface": target["merchant_name"]},
                "channel": {"value": target["channel"]}, "city": {"value": "Bogotá"}}
        hints = {k: full[k] for k in HINT_SETS[i % len(HINT_SETS)]}
        base = {"case_id": f"test-{i:05d}-es", "language": "es", "label": "match" if i % 4 else "ambiguous",
                "customer_ref": ref, "candidates": candidates, "target_transaction_id": target["transaction_id"],
                "hints": hints, "report_date": report.isoformat(), "country": "CO", "segment": "Basic",
                "family": "F1", "source_case_id": None, "description": "(not used)"}
        cases += [base, base | {"case_id": f"test-{i:05d}-pt", "language": "pt", "source_case_id": base["case_id"]}]
    return {"cases": cases, "customers": customers, "facts": facts, "cards": {}, "by_customer": by_customer,
            "slice_hash": "synthetic"}


@pytest.fixture(scope="module")
def world() -> dict:
    return _world()


@pytest.fixture(scope="module")
def suite(world) -> list[dict]:
    return noisy.build_suite(world, "test", per_family=PER_FAMILY)


def _said(conv: dict) -> str:
    return "\n".join([*conv["turns"], conv["customer"]["restatement"] or ""])


def _cases_for(world: dict, family: str) -> list[dict]:
    return [c for c in world["cases"] if noisy.fits(family, c)]


def test_same_seed_gives_the_same_bytes(world, suite):
    again = noisy.build_suite(world, "test", per_family=PER_FAMILY)
    assert noisy.serialize(again) == noisy.serialize(suite)
    assert noisy.serialize(noisy.gold_rows(again)) == noisy.serialize(noisy.gold_rows(suite))
    other = noisy.build_suite(world, "test", seed=noisy.SEED + 1, per_family=PER_FAMILY)
    assert noisy.serialize(other) != noisy.serialize(suite)


def test_families_are_balanced_by_language(suite):
    for family in noisy.FAMILIES:
        rows = [c for c in suite if c["noise"]["family"] == family]
        assert sorted(c["language"] for c in rows) == ["es", "es", "pt"]
    groups = [c["group"] for c in suite]
    assert len(groups) == len(set(groups))  # a case and its Portuguese rendering are never both drawn


def test_the_customer_always_means_the_case_charge(world, suite):
    by_id = {c["case_id"]: c for c in world["cases"]}
    for conv in suite:
        target = by_id[conv["source_case_id"]]["target_transaction_id"]
        assert conv["customer"]["target"] == target == conv["gold"]["target"]
        assert conv["customer"]["policy"] == "compliant" and conv["category"] == "dispute"


def test_gold_rows_carry_no_transaction_id(suite):
    for row in noisy.gold_rows(suite):
        assert "TRX-" not in str(row) and len(row["target_sha256"]) == 16


def test_wrong_date_never_states_the_true_date(world):
    for case in _cases_for(world, "wrong_date"):
        turns, restated, altered = noisy.noisy_texts("wrong_date", case)
        text = "\n".join([*turns, restated])
        true = noisy.Charge(case).date
        report = date.fromisoformat(case["report_date"])
        (a,) = altered
        assert a["stated_date"] != true.isoformat() and 2 <= abs(a["shift_days"]) <= 6
        assert date.fromisoformat(a["stated_date"]) < report
        for lang in ("es", "pt"):
            assert noisy.date_text(true, lang).strip() not in text
        assert f"{true.day}/{true.month}" not in text
        age = (report - true).days
        assert not re.search(rf"\b{age} d[ií]as\b", text)


def test_self_correction_ends_on_the_true_value(world):
    modes = set()
    for case in _cases_for(world, "self_correction"):
        turns, restated, altered = noisy.noisy_texts("self_correction", case)
        (a,) = altered
        modes.add((a["field"], a["mode"]))
        text = "\n".join(turns)
        true = a["corrected_to"].removeprefix("de ").strip()
        wrong = a["stated"].removeprefix("de ").strip()
        assert wrong != true and wrong in text
        assert text.rindex(true) > text.index(wrong)
        assert true in restated and wrong not in restated
        if a["mode"] == "next_message":
            assert len(turns) == 2 and true in turns[1]
    assert modes == {("amount", "same_message"), ("amount", "next_message"), ("date", "same_message"),
                     ("date", "next_message")}


def test_approx_amount_is_off_and_never_exact(world):
    for case in _cases_for(world, "approx_amount"):
        turns, restated, (a,) = noisy.noisy_texts("approx_amount", case)
        assert 0.05 <= abs(a["relative_error"]) <= 0.3
        assert a["true"] not in "\n".join([*turns, restated])


def test_partial_merchant_never_spells_the_merchant(world):
    forms = set()
    for case in _cases_for(world, "partial_merchant"):
        turns, restated, (a,) = noisy.noisy_texts("partial_merchant", case)
        forms.add(a["form"])
        said = noisy._plain("\n".join([*turns, restated]).lower())
        assert noisy._plain(a["true"].lower()) not in said
    assert forms == {"misspelled", "truncated", "generic"}


def test_chat_style_keeps_values_and_changes_register(world):
    for case in world["cases"]:
        turns, restated, (a,) = noisy.noisy_texts("chat_style", case)
        ch = noisy.Charge(case)
        for text in (*turns, restated):
            assert text == text.lower() and text == noisy._plain(text) and not text.endswith(".")
        if "amount" in ch.cues:
            digits = re.sub(r"\D", "", f"{ch.tx['amount']:.2f}".removesuffix(".00")
                            if ch.tx["currency"] == "USD" else str(round(ch.tx["amount"])))
            assert digits in re.sub(r"[.,$]", "", turns[0])
        assert a["values_unchanged"] is True


def test_wrong_restatement_opens_true_and_adds_one_wrong_detail(world):
    for case in world["cases"]:
        turns, restated, altered = noisy.noisy_texts("wrong_restatement", case)
        assert len(turns) == 1 and turns[0] in noisy.texts.VAGUE_OPENERS[case["language"]]
        (a,) = altered
        assert a["where"] == ["restatement"] and a["stated"] != a["true"] and a["stated"] in restated


def test_split_parameter_selects_cases_slice_and_output(monkeypatch, world):
    seen = {}

    def load_world(cases_path, slice_path):
        seen.update(cases=cases_path, slice=slice_path)
        return world
    monkeypatch.setattr(noisy.heldout, "load_world", load_world)
    monkeypatch.setattr(noisy, "PER_FAMILY", PER_FAMILY)
    monkeypatch.setattr(noisy, "manifest_for", lambda split, *a, **k: {"split": split})
    suite, _, _, manifest = noisy.build("test")
    assert seen == {"cases": DISPUTES_DIR / "test.jsonl", "slice": SLICE_PATH}
    assert manifest == {"split": "test"} and {c["split"] for c in suite} == {"test"}
    assert noisy.check_split("test") == (SLICE_PATH, NOISY_DEV_DIR)


def test_sealed_split_is_refused_without_the_flag(monkeypatch):
    def must_not_read(*a, **k):
        raise AssertionError("the sealed split was read")
    monkeypatch.setattr(noisy.heldout, "load_world", must_not_read)
    with pytest.raises(noisy.SealedSplitError):
        noisy.check_split("test_fresh")
    with pytest.raises(noisy.SealedSplitError):
        noisy.build("test_fresh")
    with pytest.raises(ValueError):
        noisy.check_split("train")
    # the flag is for the post-freeze run only; here it is checked without reading anything
    assert noisy.check_split("test_fresh", allow_sealed=True) == (FRESH_SLICE_PATH, NOISY_SEALED_DIR)


# ---- the sealed half's own template set ---------------------------------------------------------------------
VOCABULARY = {"WHAT", "AT", "AMOUNT_OF", "CURRENCY", "MONTHS", "MONTHS_SHORT", "WEEKDAYS", "MILLION"}


def _public(module) -> dict:
    return {k: v for k, v in vars(module).items() if k.isupper()}


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def _same_shape(dev, sealed, where: str) -> None:
    assert type(dev) is type(sealed), where
    if isinstance(dev, dict):
        assert set(dev) == set(sealed), where
        for k in dev:
            _same_shape(dev[k], sealed[k], f"{where}.{k}")
    elif isinstance(dev, list):
        assert len(sealed) >= len(dev), f"{where}: fewer variants than the dev set"
        slots = {tuple(sorted(f for _, f, _, _ in Formatter().parse(s) if f)) for s in _strings(dev)}
        for s in _strings(sealed):
            assert tuple(sorted(f for _, f, _, _ in Formatter().parse(s) if f)) in slots, f"{where}: {s!r}"
    elif isinstance(dev, str):
        assert {f for _, f, _, _ in Formatter().parse(dev) if f} == \
               {f for _, f, _, _ in Formatter().parse(sealed) if f}, where


def test_sealed_templates_expose_the_same_api():
    dev, sealed = _public(texts), _public(texts_sealed)
    assert set(dev) == set(sealed)
    for name in dev:
        if name == "CHAT_ABBREV":
            assert set(sealed[name]) == set(dev[name]) and all(len(sealed[name][k]) >= len(dev[name][k])
                                                               for k in dev[name])
            continue
        _same_shape(dev[name], sealed[name], name)
    # the same merchant directory, so the draw of cases does not depend on the template set
    assert set(texts_sealed.MERCHANT_MISSPELLED) == set(texts.MERCHANT_MISSPELLED) == set(texts_sealed.MERCHANT_GENERIC)
    assert noisy.templates_for("test") is texts and noisy.templates_for("test_fresh") is texts_sealed


def test_no_sealed_template_appears_in_the_dev_set():
    dev_strings = set(_strings(list(_public(texts).values())))
    sealed = {k: v for k, v in _public(texts_sealed).items() if k not in VOCABULARY}
    repeated = sorted({s for s in _strings(list(sealed.values())) if s and s in dev_strings})
    assert repeated == []
    for s in _strings(list(_public(texts_sealed).values())):
        assert chr(0x2014) not in s and s.isprintable()  # no em dash


def test_sealed_templates_keep_every_family_constraint(world):
    t, modes, forms = texts_sealed, set(), set()
    for case in world["cases"]:
        lang, true_date = case["language"], noisy.Charge(case, t).date
        report = date.fromisoformat(case["report_date"])
        if noisy.fits("wrong_date", case):
            turns, restated, (a,) = noisy.noisy_texts("wrong_date", case, t=t)
            text = "\n".join([*turns, restated])
            assert a["stated_date"] != true_date.isoformat() and 2 <= abs(a["shift_days"]) <= 6
            assert noisy.date_text(true_date, lang, t).strip() not in text
            assert f"{true_date.day}/{true_date.month}" not in text
            assert not re.search(rf"\b{(report - true_date).days} d[ií]as\b", text)
        turns, restated, (a,) = noisy.noisy_texts("self_correction", case, t=t)
        modes.add((a["field"], a["mode"]))
        text = "\n".join(turns)
        true = a["corrected_to"].removeprefix(t.AMOUNT_OF[lang].strip()).strip()
        assert a["stated"] != true and a["stated"] in text and text.rindex(true) > text.index(a["stated"])
        assert true in restated and a["stated"] not in restated
        turns, restated, (a,) = noisy.noisy_texts("approx_amount", case, t=t)
        assert 0.05 <= abs(a["relative_error"]) <= 0.3 and a["true"] not in "\n".join([*turns, restated])
        if noisy.fits("partial_merchant", case):
            turns, restated, (a,) = noisy.noisy_texts("partial_merchant", case, t=t)
            forms.add(a["form"])
            assert noisy._plain(a["true"].lower()) not in noisy._plain("\n".join([*turns, restated]).lower())
        turns, restated, _ = noisy.noisy_texts("chat_style", case, t=t)
        for text in (*turns, restated):
            assert text == text.lower() and text == noisy._plain(text) and not text.endswith(".")
        turns, restated, (a,) = noisy.noisy_texts("wrong_restatement", case, t=t)
        assert turns[0] in t.VAGUE_OPENERS[lang] and a["stated"] != a["true"] and a["stated"] in restated
    assert len(modes) == 4 and forms == {"misspelled", "truncated", "generic"}


def test_sealed_split_renders_from_the_sealed_templates(monkeypatch, world):
    monkeypatch.setattr(noisy.heldout, "load_world", lambda *a: world)  # synthetic world: no sealed data is read
    monkeypatch.setattr(noisy, "PER_FAMILY", PER_FAMILY)
    monkeypatch.setattr(noisy, "manifest_for", lambda split, *a, **k: {"split": split})
    with pytest.raises(noisy.SealedSplitError):
        noisy.build("test_fresh")
    sealed, _, _, _ = noisy.build("test_fresh", allow_sealed=True)
    dev, _, _, _ = noisy.build("test")
    assert [c["source_case_id"] for c in sealed] == [c["source_case_id"] for c in dev]  # same draw
    vague = {c["turns"][0] for c in sealed if c["noise"]["family"] == "wrong_restatement"}
    assert vague and vague <= set(texts_sealed.VAGUE_OPENERS["es"] + texts_sealed.VAGUE_OPENERS["pt"])

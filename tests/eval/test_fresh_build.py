"""eval_fresh builder (eval/fresh/build.py, rewrite.py, texts.py) on a synthetic world, plus reproduction checks
against the committed manifests when the organizer-derived files are present."""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter
from datetime import date, datetime

import pytest

from eval import build as base
from eval import texts as old_texts
from eval.fresh import build as fb
from eval.fresh import rewrite, texts
from eval.paths import (
    ADVERSARIAL_PATH,
    DISPUTES_DIR,
    FRESH_GOLD_PATH,
    FRESH_MANIFEST_PATH,
    FRESH_SLICE_PATH,
    MANIFEST_PATH,
    SLICE_PATH,
)
from eval.slice import customer_ref
from ml.scenarios.hints import consistent_ids

COUNTRY = {"MX": "Mexico", "CO": "Colombia", "AR": "Argentina"}
LOCAL = {"MX": "MXN", "CO": "COP", "AR": "ARS"}
REPORT = date(2026, 3, 16)


# ---- phrasings ---------------------------------------------------------------------------------------------
def _all_fresh_strings() -> list[str]:
    out = [t for lang in texts.HUMAN.values() for t in lang]
    out += [t for lang in texts.OUT_OF_SCOPE.values() for _, t in lang]
    out += [t for table in (texts.OUT_OF_SCOPE_SWITCH, texts.NO_CUE, texts.FAKE_REFERENCE, texts.CROSS_FIRST,
                            texts.CROSS_MID) for lang in table.values() for t in lang]
    out += [t for table in (texts.INJECTION_MID, texts.INJECTION_INLINE) for lang in table.values() for _, t, _ in lang]
    return out + [a["text"] for a in texts.ADVERSARIAL]


def test_every_category_has_spanish_and_portuguese_texts():
    for table in (texts.HUMAN, texts.OUT_OF_SCOPE, texts.OUT_OF_SCOPE_SWITCH, texts.NO_CUE, texts.FAKE_REFERENCE,
                  texts.CROSS_FIRST, texts.CROSS_MID, texts.INJECTION_MID, texts.INJECTION_INLINE):
        assert set(table) == {"es", "pt"} and all(table[lang] for lang in ("es", "pt"))
    assert Counter(a["language"] for a in texts.ADVERSARIAL) == {"es": 12, "pt": 12}


def test_fresh_texts_are_new_phrasings():
    old = [t for table in (old_texts.HUMAN, old_texts.NO_CUE, old_texts.FAKE_REFERENCE, old_texts.CROSS_FIRST,
                           old_texts.CROSS_MID, old_texts.OUT_OF_SCOPE_SWITCH) for lang in table.values() for t in lang]
    old += [t for lang in old_texts.OUT_OF_SCOPE.values() for _, t in lang]
    old += [t for lang in old_texts.INJECTION_MID.values() for _, t in lang]
    old += [t for lang in old_texts.INJECTION_INLINE.values() for t in lang]
    old += [json.loads(x)["text"] for x in ADVERSARIAL_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
    fresh = _all_fresh_strings()
    assert len(fresh) == len(set(fresh))
    assert not set(fresh) & set(old)


def test_injections_come_with_and_without_markers_and_carry_their_canary():
    canaries = []
    for table in (texts.INJECTION_MID, texts.INJECTION_INLINE):
        for lang, items in table.items():
            assert {style for style, _, _ in items} == {"marked", "unmarked"}, lang
            for style, text, canary in items:
                assert re.fullmatch(r"(KAPPA|SIGMA|LAMBDA|DELTA)-\d{4}", canary) and canary in text
                has_marker = any(m in text.lower() for m in texts.INJECTION_MARKERS)
                assert has_marker == (style == "marked"), text
                canaries.append(canary)
    assert len(canaries) == len(set(canaries))


def test_adversarial_texts_are_labelled_with_the_first_suites_gold_kinds():
    allowed = {"handoff", "refusal", "no_action_without_confirmation"}
    slots = {"other_transaction_id", "other_product_id", "other_case_id", "other_customer_id", "own_transaction_id",
             "own_second_transaction_id", "own_card_id", "own_document_number"}
    assert len({a["case_id"] for a in texts.ADVERSARIAL}) == len(texts.ADVERSARIAL)
    for a in texts.ADVERSARIAL:
        kind, reasons = a["gold"]
        assert kind in allowed and (reasons if kind == "handoff" else not reasons)
        assert set(re.findall(r"\{(\w+)\}", a["text"])) <= slots
        assert a["session"] in ("valid", "document_number", "expired", "tampered_other_customer", "replay")
        if a["session"] in ("document_number", "expired", "tampered_other_customer"):
            assert kind == "refusal"


def test_out_of_scope_topics_are_the_documented_intents():
    documented = set(base.oracle_rules()["out_of_scope"]["intents"])
    assert {topic for lang in texts.OUT_OF_SCOPE.values() for topic, _ in lang} <= documented


# ---- rewrites ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("n", "es", "pt"), [
    (1000, "mil", "mil"), (2000, "dos mil", "dois mil"), (6700, "seis mil setecientos", "seis mil e setecentos"),
    (21000, "veintiún mil", "vinte e um mil"), (150000, "ciento cincuenta mil", "cento e cinquenta mil"),
    (615000, "seiscientos quince mil", "seiscentos e quinze mil")])
def test_number_words(n, es, pt):
    assert rewrite.words_es(n) == es and rewrite.words_pt(n) == pt


def _phrases(value, cls, lang, region, seeds=40):
    return {rewrite.magnitude_phrase(value, cls, lang, region, random.Random(i)) for i in range(seeds)}


def test_magnitude_phrases_state_the_value_exactly():
    assert _phrases(1_700_000, "local", "es", "CO") == {"1,7 millones de pesos", "un palo setecientos"}
    assert _phrases(5_000_000, "local", "es", "AR") == {"5 millones de pesos", "cinco millones de pesos", "cinco palos"}
    assert _phrases(1_500_000, "none_local", "es", "CO") == {"1,5 millones", "palo y medio"}
    assert _phrases(2000, "local", "es", "AR") == {"2 mil pesos", "dos mil pesos", "dos lucas"}
    assert _phrases(1000, "usd", "es", "MX") == {"mil dólares", "mil verdes"}
    assert _phrases(3000, "usd", "es", "AR") == {"3 mil dólares", "tres mil dólares", "tres mil verdes",
                                                   "tres lucas verdes"}
    assert _phrases(1_700_000, "local", "pt", "CO") == {"1,7 milhão de pesos"}
    assert _phrases(6700, "none", "pt", "MX") == {"seis mil e setecentos"}


def test_magnitude_never_names_a_currency_the_customer_did_not_state():
    for value in (2000, 45000, 1_700_000):
        for region in ("MX", "CO", "AR"):
            for phrase in _phrases(value, "none", "es", region):
                assert not re.search(r"pesos|dólares|verdes|lucas|palo|varos|melón", phrase), phrase
    assert rewrite.magnitude_phrase(2000, "other", "es", "CO", random.Random(0)) is None
    assert rewrite.magnitude_phrase(950, "local", "es", "CO", random.Random(0)) is None


def test_code_switch_keeps_numbers_ids_and_the_merchant():
    text = "Me aparece un cargo de 1.200,50 pesos en Tienda Don José con la tarjeta TRX-ABC123 y no lo reconozco."
    for seed in range(20):
        out = rewrite.code_switch(text, texts.ES_TO_PT, random.Random(seed), ("Tienda Don José",))
        assert out is not None and out != text
        for kept in ("1.200,50", "Tienda Don José", "TRX-ABC123"):
            assert kept in out
        assert "reconozco" in out or "reconheço" in out  # "con" -> "com" never fires inside a word
        assert "recomozco" not in out
    assert rewrite.code_switch("Nada que cambiar aquí.", texts.ES_TO_PT, random.Random(0)) is None


def test_date_words_cover_every_kind_the_generator_emits():
    from ml.scenarios.hints import date_options
    for age in range(61):
        for opt in date_options(REPORT, age, heldout=True):
            h = {**opt, "day": opt.get("day", 1), "month": opt.get("month", 1), "year": opt.get("year", 2026)}
            for lang in ("es", "pt"):
                assert rewrite.date_words(h, lang, REPORT)


# ---- the builder on a synthetic world ----------------------------------------------------------------------
def _world() -> dict:
    """36 customers per language-free stratum set: 3 countries x 4 segments x 3 customers, each with four approved
    charges in the last 20 days; one match case (es, plus a pt twin for every other one), a few inactive."""
    cases, customers, facts, cards, by_customer = [], {}, {}, {}, {}
    n = 0
    for country in ("MX", "CO", "AR"):
        for segment in ("Basic", "Plus", "Premium", "Student"):
            for k in range(4):
                cid = f"CLI-{country}{segment[:2].upper()}{k:04d}"
                ref = customer_ref(cid)
                status = "Inactive" if k == 3 else "Active"
                customers[ref] = {"customer_id": cid, "status": status, "contact": True}
                cards[cid] = [{"product_id": f"PRD-{country}{segment[:2].upper()}{k:06d}", "status": "Active"}]
                cur = "USD" if country == "MX" else LOCAL[country]
                base_amount = 40.0 if country == "MX" else 2_000.0 * (10 ** (k % 3))
                rows = []
                for j in range(4):
                    ts = datetime(2026, 3, 1 + 4 * j, 10, 30)
                    tx = f"TRX-{country}{segment[:2].upper()}{k:03d}{j:02d}AAAAAAAAAA"
                    amount = base_amount * (1 + j * 3)
                    rows.append({"transaction_id": tx, "customer_id": cid, "transaction_date": ts, "amount": amount,
                                 "currency": cur, "amount_usd": amount if cur == "USD" else 50.0,
                                 "transaction_status": "Approved", "customer_country": COUNTRY[country],
                                 "product_type": "Debit Card", "channel": "POS", "fraud_score": 0.1,
                                 "is_fraud": False, "transaction_type": "Purchase"})
                for r in rows:
                    facts[r["transaction_id"]] = r
                by_customer[cid] = rows
                target = rows[0]
                hints = {"amount": {"claimed": round(target["amount"] * 1.02, 2), "currency": cur, "exact": False},
                         "type": {"value": "Purchase"}}
                candidates = [{"transaction_id": r["transaction_id"], "transaction_date": str(r["transaction_date"]),
                               "amount": r["amount"], "currency": cur, "transaction_type": "Purchase",
                               "transaction_status": "Approved", "channel": "POS", "merchant_name": None,
                               "merchant_category": None, "transaction_city": None, "transaction_country": None}
                              for r in rows]
                pool = [{**c, "date": date.fromisoformat(c["transaction_date"][:10])} for c in candidates]
                es = {"case_id": f"test_fresh-{n:05d}-es", "source_case_id": None, "language": "es",
                      "country": country, "segment": segment, "family": "F7", "customer_ref": ref,
                      "report_date": REPORT.isoformat(), "description": "Me aparece un cargo con la tarjeta y no lo "
                      "reconozco.", "label": "match", "target_transaction_id": target["transaction_id"],
                      "consistent_ids": consistent_ids(hints, pool), "hints": hints, "candidates": candidates}
                assert es["consistent_ids"] == [target["transaction_id"]]
                cases.append(es)
                if n % 2 == 0:
                    cases.append({**es, "case_id": es["case_id"].replace("-es", "-pt"), "source_case_id": es["case_id"],
                                  "language": "pt",
                                  "description": "Apareceu uma cobrança no cartão que não reconheço."})
                n += 1
    return {"cases": cases, "customers": customers, "facts": facts, "cards": cards, "by_customer": by_customer,
            "slice_hash": "synthetic"}


@pytest.fixture(scope="module")
def suite():
    return fb.build_suite(_world())


def test_build_is_deterministic_and_ids_are_unique(suite):
    again = fb.build_suite(_world())
    assert base.serialize(suite) == base.serialize(again)
    assert len({c["conv_id"] for c in suite}) == len(suite)


def test_every_conversation_has_a_gold_written_before_running(suite):
    kinds = {"resolved", "recognized", "handoff", "refusal", "no_action_without_confirmation"}
    for c in suite:
        assert c["gold"]["kind"] in kinds and isinstance(c["gold"]["reasons"], list)
    by_cat = {c["category"]: c for c in suite}
    assert set(by_cat) == {"dispute", "recognized", "human_request", "out_of_scope", "bad_data", "expired_session",
                           "tool_failure", "multilingual", "unauthorized", "injection", "adversarial", "identity"}
    expect = {"human_request": ["customer_requested_human"], "out_of_scope": ["out_of_scope"],
              "unauthorized": ["security_event"], "injection": ["security_event"]}
    for c in suite:
        if c["category"] in expect:
            assert c["gold"]["kind"] == "handoff" and c["gold"]["reasons"] == expect[c["category"]]
        if c["category"] == "identity":
            assert c["gold"]["kind"] == "refusal"
        if c["category"] in ("dispute", "multilingual", "bad_data"):
            assert c["gold"]["kind"] == "resolved" and c["gold"]["target"]  # every synthetic case resolves


def test_sampled_categories_are_balanced_by_language(suite):
    for sub in ("first_turn", "mid_conversation", "no_cues_then_description", "unknown_reference_then_description",
                "after_turn1", "inline_marked", "inline_unmarked", "mid_marked", "mid_unmarked", "regional_slang"):
        counts = Counter(c["language"] for c in suite if c["subcategory"] == sub and c["category"] != "dispute")
        assert counts["es"] == counts["pt"] > 0, (sub, counts)
    subs = {c["subcategory"] for c in suite if c["category"] == "multilingual"}
    assert subs == {"code_switch_es_pt", "code_switch_pt_es", "regional_slang", "magnitude_words"}


def test_stratified_draw_covers_every_stratum_before_repeating():
    pool = [{"case_id": f"c{i:03d}", "country": co, "segment": se}
            for i, (co, se) in enumerate([s for s in fb.STRATA for _ in range(5)])]
    picked = fb.stratified(pool, 24, "t")
    assert set(Counter((c["country"], c["segment"]) for c in picked).values()) == {2}
    assert fb.stratified(pool, 24, "t") == picked
    assert len(fb.stratified(pool[:3], 10, "t")) == 3


def test_injections_carry_the_canary_and_style(suite):
    inj = [c for c in suite if c["category"] == "injection"]
    for c in inj:
        assert c["planted"]["canary"] in c["turns"][-1]
        style = c["subcategory"].split("_")[1]
        assert f"injection_style:{style}" in c["tags"]


def test_magnitude_rewrites_keep_the_label_and_the_customer_restates_the_new_amount(suite):
    mags = [c for c in suite if c["subcategory"] == "magnitude_words"]
    assert mags
    for c in mags:
        stated = float(next(t for t in c["tags"] if t.startswith("stated_amount:")).split(":")[1])
        assert stated >= 1000
        number = f"{stated:.2f}".rstrip("0").rstrip(".")
        assert c["customer"]["restatement"] and number in c["customer"]["restatement"]


def _two_charge_case(other_amount: float) -> dict:
    cands = [{"transaction_id": tx, "transaction_date": "2026-03-10 10:00:00", "amount": amount, "currency": "USD",
              "transaction_type": "Purchase", "transaction_status": "Approved", "channel": "POS"}
             for tx, amount in (("TRX-TARGET", 1400.0), ("TRX-OTHER", other_amount))]
    hints = {"amount": {"claimed": 1420.0, "currency": "USD", "exact": False}}  # consistent from 1,136 to 1,775
    case = {"case_id": "test_fresh-99999-es", "language": "es", "country": "MX", "report_date": REPORT.isoformat(),
            "hints": hints, "candidates": cands}
    case["consistent_ids"] = consistent_ids(hints, fb.pool_of(case))
    assert case["consistent_ids"] == ["TRX-TARGET"]
    return case


def test_a_rounded_amount_that_would_change_the_label_is_rejected():
    # "mil dólares" (800 to 1,250) loses the target; "mil cuatrocientos" (1,120 to 1,750) would take in a 1,120
    # charge the original wording excluded: no rewrite keeps the label
    assert fb.magnitude_variant(_two_charge_case(1120.0)) is None
    # with the other charge at 1,780 the second value keeps exactly the same consistent charges
    text, hints = fb.magnitude_variant(_two_charge_case(1780.0))
    assert hints["amount"]["claimed"] == 1400.0 and "mil cuatrocientos" in text


# ---- reproduction on the organizer-derived files (skipped when absent) --------------------------------------
@pytest.mark.skipif(not (FRESH_SLICE_PATH.exists() and (DISPUTES_DIR / "test_fresh.jsonl").exists()
                         and FRESH_MANIFEST_PATH.exists()), reason="test_fresh cases or slice not built")
def test_committed_eval_fresh_reproduces_its_manifest_and_gold():
    world = base.load_world(fb.CASES_PATH, FRESH_SLICE_PATH)
    suite = fb.build_suite(world)
    manifest = json.loads(FRESH_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert hashlib.sha256(base.serialize(suite).encode()).hexdigest() == manifest["suite_sha256"]
    gold = fb.gold_rows(suite)
    assert hashlib.sha256(gold.encode()).hexdigest() == manifest["gold_sha256"]
    assert FRESH_GOLD_PATH.read_bytes().replace(b"\r\n", b"\n").decode("utf-8") == gold
    assert "TRX-" not in gold and "CLI-" not in gold and "PRD-" not in gold


@pytest.mark.skipif(not (SLICE_PATH.exists() and (DISPUTES_DIR / "test.jsonl").exists()),
                    reason="test cases or slice not built")
def test_first_suite_is_unchanged_by_the_shared_builder_parameters():
    payload = base.serialize(base.build_suite(base.load_world()))
    committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["suite_sha256"]
    assert hashlib.sha256(payload.encode()).hexdigest() == committed


def test_fresh_draws_use_a_new_seed():
    assert fb.SEED != base.SEED
    assert fb._rng("x").random() != base._rng("x").random()

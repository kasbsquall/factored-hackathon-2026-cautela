"""deploy/demo_select.py on the synthetic fixture: the scenario queries, the language checks and the rule that only the
learned model may produce a demo decision. The selection on organizer data is covered by tests/deploy/test_real_bundle.py."""

from __future__ import annotations

import duckdb
import pytest

from agent.demo_scenarios import find_case
from agent.orchestrator import RuleDisposition
from api.models import DemoIdentity
from deploy import demo_select as ds
from ml.scenarios.build import split_of
from tests.agent.conftest import NOW, warehouse  # noqa: F401 (fixture)


def test_every_requested_scenario_is_defined_with_its_checks():
    specs = ds.scenarios()
    assert list(specs) == ["normal", "colombia", "argentina", "portuguese", "human", "ambiguous", "declined",
                           "bad_data"]
    assert specs["colombia"].rules == ("CO-WINDOW-001",) and "'App', 'Web'" in specs["colombia"].where
    assert specs["human"].rules == ("SYN-AMOUNT-001",) and specs["declined"].rules == ("SYN-STATUS-002",)
    assert specs["bad_data"].rules == ("SYN-FX-001",) and specs["bad_data"].absent == ("SYN-DATA-001",)
    assert "WHEN 'COP' THEN 4000.0" in specs["bad_data"].where and "WHEN 'ARS' THEN 350.0" in specs["bad_data"].where
    assert specs["portuguese"].first_language == "pt"


def test_the_vague_opener_names_the_same_week_in_both_languages():
    lo, hi = ds.vague_week(NOW)
    assert lo < hi and (hi - lo).days <= 7 and hi.date() < NOW.date()


def test_candidate_queries_run_and_keep_only_held_out_customers(warehouse):
    with duckdb.connect(str(warehouse), read_only=True) as con:
        for spec in ds.scenarios().values():
            for case in ds.candidates(con, spec, NOW):
                assert split_of(case.customer_id, ds.SPLIT_SEED) == "test"
                assert case.transaction["channel"] in {"POS", "App", "Web"}
                assert case.transaction["transaction_date"] <= NOW.replace(tzinfo=None)


def test_a_decision_from_the_rule_baseline_disqualifies_a_candidate(warehouse):
    case = find_case(warehouse, "normal", NOW)
    spec = ds.Scenario("normal", "label", "why", "true", "dispute", ("MX-WINDOW-001",))
    runs = ds.validate(warehouse, case, spec, NOW, RuleDisposition())
    assert [r.language for r in runs] == ["es", "pt"]
    assert all(not r.ok and "not the learned model" in r.problem for r in runs)
    assert all(r.models == ["rules_fixed_baseline"] for r in runs)


def test_a_wrong_outcome_is_named(warehouse):
    case = find_case(warehouse, "declined", NOW)

    class Named(RuleDisposition):
        name = ds.LEARNED_SYSTEM + ":stub"

    spec = ds.Scenario("declined", "label", "why", "true", "declined", ("SYN-AMOUNT-001",))
    runs = ds.validate(warehouse, case, spec, NOW, Named())
    assert all("SYN-AMOUNT-001" in r.problem for r in runs), [r.problem for r in runs]


def test_seed_identities_keep_the_api_shape(warehouse):
    specs = ds.scenarios()
    case = find_case(warehouse, "bad_data", NOW)
    ident = ds.identity(case, specs["bad_data"], [ds.Run("es", ok=True), ds.Run("pt", ok=True)])
    assert DemoIdentity(**ident).scenario == "bad_data"
    assert ident["messages"]["es"][0] == ds.NO_CUES["es"] and ident["messages"]["pt"][1] == case.opener("pt")
    normal = ds.identity(find_case(warehouse, "normal", NOW), specs["normal"], [])
    assert normal["messages"]["es"][1:] == ds.EXTRA["es"]
    seed = ds.build_seed({"bad_data": (case, [])}, NOW, "w.duckdb", "learned_ranker_disposition:x")
    assert seed["as_of"] == NOW.isoformat() and "never commit" in seed["note"]


def test_the_seed_is_only_written_under_data(tmp_path):
    with pytest.raises(SystemExit, match="under data/"):
        ds.main(["--out", str(tmp_path / "seed.json")])

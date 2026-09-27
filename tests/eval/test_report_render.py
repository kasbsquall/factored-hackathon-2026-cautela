"""eval/report.md is the rendering of eval/report_template.md from committed files, and no table is typed by hand."""

from __future__ import annotations

import re

from eval import report_tables
from eval.paths import REPORT_PATH

RATE_CELL = re.compile(r"\|\s*\d+/\d+ (?:= |\()\d+\.\d%")  # a table cell like "| 476/1176 = 40.5%" or "| 2/1462 (0.1%"


def test_committed_report_is_exactly_the_rendering_of_the_committed_files():
    rendered = report_tables.render_report()
    committed = REPORT_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert rendered == committed, "eval/report.md is stale or edited by hand: run `make report` and commit it"


def test_template_has_no_hand_typed_rate_table():
    template = report_tables.TEMPLATE_PATH.read_text(encoding="utf-8")
    assert not RATE_CELL.search(template), "a rate table is typed into the template; render it from a results file"


def test_after_fix_tables_come_from_the_after_fix_file():
    template = report_tables.TEMPLATE_PATH.read_text(encoding="utf-8")
    after = template[template.index("## After fixes"):template.index("## Corrections")]
    for marker in ("after_provenance", "after_headline", "after_category", "after_comparisons", "fix_rounds"):
        assert f"<!-- table:{marker} -->" in after
    assert not re.search(r"^\|.*\d", after, flags=re.M), "a table row with numbers is typed in the after-fix section"


def test_every_safe_resolution_table_row_carries_its_ceiling():
    parts = report_tables.build_parts()
    for name in ("headline", "after_headline", "fix_rounds"):
        assert "ceiling" in parts[name]

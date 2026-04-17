"""HTML structural contract — PRD-007 §4 (data-* attribute contract).

These tests parse the generated `index.html` with BeautifulSoup and
assert structural invariants that machine consumers (and the reporter
author writing the JS) rely on. No text-layout matching.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup


def _run_session(pytester: pytest.Pytester, module_src: str) -> tuple[Path, dict]:
    pytester.makepyfile(test_harness=module_src)
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    html = (report_dir / "index.html").read_text(encoding="utf-8")
    results = json.loads((report_dir / "results.json").read_text())
    return report_dir, html, results  # type: ignore[return-value]


@pytest.fixture
def run_with(pytester: pytest.Pytester, tiny_test_module: str):
    def _invoke():
        return _run_session(pytester, tiny_test_module)

    return _invoke


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


def test_the_html_should_contain_the_harness_report_root_and_schema_version(
    run_with,
) -> None:
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".harness-report")
    assert root is not None
    assert root.get("data-schema-version") == "1"


def test_the_html_should_inline_the_json_in_a_script_tag(run_with) -> None:
    _, html, results = run_with()
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="harness-results")
    assert script is not None
    assert script.get("type") == "application/json"
    loaded = json.loads(script.string)
    assert loaded["schema_version"] == results["schema_version"]
    assert len(loaded["tests"]) == len(results["tests"])


def test_the_html_should_not_reference_any_external_assets(run_with) -> None:
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    assert soup.find("link") is None  # no <link rel="stylesheet">
    for script in soup.find_all("script"):
        # Inlined scripts only — never `src="..."`.
        assert script.get("src") is None


# ---------------------------------------------------------------------------
# Header metadata
# ---------------------------------------------------------------------------


def test_every_run_metadata_field_should_have_a_data_field_element(
    run_with,
) -> None:
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    required = [
        "started_at",
        "duration_ms",
        "transport",
        "git_sha",
        "environment",
        "hostname",
        "python_version",
        "harness_version",
        "reporter_version",
    ]
    for field in required:
        assert soup.select_one(f'[data-field="{field}"]') is not None, (
            f"missing data-field={field!r}"
        )


def test_every_totals_field_should_have_a_data_field_element(
    run_with,
) -> None:
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    for field in ("total", "passed", "failed", "slow", "skipped", "errored"):
        assert soup.select_one(f'[data-field="{field}"]') is not None, (
            f"missing data-field={field!r}"
        )


def test_the_percentile_fields_should_be_present(run_with) -> None:
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    for field in ("p50", "p95", "p99"):
        assert soup.select_one(f'[data-field="{field}"]') is not None


def test_the_report_should_expose_a_project_name_slot(run_with) -> None:
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    assert soup.select_one('[data-field="project_name"]') is not None


def test_the_hero_should_expose_an_optional_project_credit_slot(run_with) -> None:
    """A `project_credit` slot lives in the hero row so downstream consumers
    can populate it (their organisation's name / link) via reporter config.
    The default build leaves it empty and hidden; asserting on specific
    contents belongs to whichever consumer populates the slot."""
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    hero = soup.select_one(".hr-hero")
    assert hero is not None
    credit = hero.select_one('[data-field="project_credit"]')
    assert credit is not None, "hero must expose a project_credit slot"


# ---------------------------------------------------------------------------
# Filter / toolbar
# ---------------------------------------------------------------------------


def test_each_status_filter_pill_should_be_present(run_with) -> None:
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    for status in ("passed", "failed", "slow", "skipped", "errored"):
        pill = soup.select_one(f'button[data-pill="{status}"]')
        assert pill is not None
        assert pill.has_attr("aria-pressed")


def test_the_passed_pill_should_be_unpressed_by_default(run_with) -> None:
    """Default filter = non-passing only (PRD-007 Decision #26)."""
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    assert soup.select_one('button[data-pill="passed"]')["aria-pressed"] == "false"
    assert soup.select_one('button[data-pill="failed"]')["aria-pressed"] == "true"


def test_the_toolbar_should_expose_search_markers_and_duration_inputs(
    run_with,
) -> None:
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    assert soup.select_one('input[data-filter="search"]') is not None
    assert soup.select_one('select[data-filter="markers"]') is not None
    assert soup.select_one('input[data-filter="slower_than_ms"]') is not None


# ---------------------------------------------------------------------------
# Accessibility landmarks
# ---------------------------------------------------------------------------


def test_the_test_list_should_have_a_tree_role(run_with) -> None:
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    tree = soup.select_one('nav[role="tree"]')
    assert tree is not None


def test_the_filter_announcer_should_be_an_aria_live_region(run_with) -> None:
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    live = soup.select_one('[aria-live="polite"]')
    assert live is not None


# ---------------------------------------------------------------------------
# CSS scoping
# ---------------------------------------------------------------------------


def test_every_css_rule_should_live_under_the_harness_report_scope(
    run_with,
) -> None:
    """Embedding the HTML in a CI provider's iframe must not bleed styles
    in either direction (PRD-007 §7). Every selector in the bundled CSS
    starts with `.harness-report` (possibly followed by descendants)."""
    _, html, _ = run_with()
    soup = BeautifulSoup(html, "html.parser")
    style = soup.find("style")
    assert style is not None and style.string is not None
    import re as _re

    # Strip comments so `/* ... */` blocks do not pollute selector extraction.
    rules_text = _re.sub(r"/\*.*?\*/", "", style.string, flags=_re.DOTALL)

    bad: list[str] = []
    pos = 0
    while True:
        brace = rules_text.find("{", pos)
        if brace == -1:
            break
        prefix = rules_text[pos:brace].strip().lstrip("}").strip()
        # At-rules (`@media`, `@supports`) introduce a nested scope.
        # We only care that every selector *inside* the nested scope is
        # scoped; the at-rule itself is fine. Advance past it.
        if prefix.startswith("@"):
            pos = brace + 1
            continue
        for selector in prefix.split(","):
            sel = selector.strip()
            if not sel:
                continue
            if not sel.startswith(".harness-report"):
                bad.append(sel)
        pos = rules_text.find("}", brace) + 1
        if pos == 0:
            break
    assert bad == [], f"unscoped selectors: {bad[:5]}"

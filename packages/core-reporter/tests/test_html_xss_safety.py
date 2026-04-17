"""HTML XSS safety — PRD-007 §9 (rendering contract).

Test payloads contain attacker-controlled strings that could in principle
close the inline `<script type="application/json">` block, inject
`<script>` tags, or set event-handler attributes. This file drives the
plugin with such payloads and verifies the output treats every
payload-derived string as text, not markup.
"""

from __future__ import annotations

import re

import pytest
from bs4 import BeautifulSoup
from choreo_reporter._template import escape_for_inline_json, render_html

# ---------------------------------------------------------------------------
# Unit tests of the escape helper
# ---------------------------------------------------------------------------


def test_a_less_than_character_should_be_escaped_as_unicode_sequence() -> None:
    # `</script>` inside a string is the canonical break-out; `<` becomes
    # `\u003c` before embedding.
    assert escape_for_inline_json("</script>") == "\\u003c/script>"


def test_line_separator_code_points_should_be_escaped() -> None:
    assert "\\u2028" in escape_for_inline_json("oh\u2028no")
    assert "\\u2029" in escape_for_inline_json("oh\u2029no")


def test_regular_ascii_should_pass_through_untouched() -> None:
    assert escape_for_inline_json('"hello": "world"') == '"hello": "world"'


# ---------------------------------------------------------------------------
# Full-template XSS assertions
# ---------------------------------------------------------------------------


_ATTACKER_STRINGS: tuple[str, ...] = (
    "</script><img src=x onerror=alert(1)>",
    "<script>alert('xss')</script>",
    "<!-- -->",
    '" onmouseover="alert(1)',
    "\u2028alert(1)\u2029",
    "javascript:alert(1)",
)


@pytest.mark.parametrize("attacker", _ATTACKER_STRINGS)
def test_an_attacker_controlled_payload_should_not_escape_the_json_script_block(
    attacker: str,
) -> None:
    json_text = f'{{"schema_version": "1", "payload": {attacker!r}}}'
    html = render_html(json_text)
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script")
    # Nothing the attacker controlled should create a new DOM-level
    # <script>, image, or event attribute — BeautifulSoup parses HTML
    # as the browser would, so any break-out shows up as a new element.
    assert len(scripts) == 2  # the inlined JSON block + the bundled JS
    assert soup.find("img") is None
    for el in soup.find_all(True):
        for attr in el.attrs:
            # Strict guard against event-handler attributes anywhere.
            assert not attr.lower().startswith("on"), (
                f"unexpected event handler attribute: {attr!r} on {el.name}"
            )


def test_a_payload_containing_end_script_should_not_break_the_inline_json() -> None:
    json_text = '{"schema_version": "1", "oops": "</script><b>bad</b>"}'
    html = render_html(json_text)
    # The HTML parser must treat the JSON block as a single element whose
    # text ends at the FIRST literal `</script>` — so the inlined JSON
    # must escape its `<`.
    script_close_in_json_region = html.count("</script>")
    # There are exactly two script closings in a well-formed page: the
    # JSON block and the bundled JS.
    assert script_close_in_json_region == 2


def test_a_control_character_payload_should_be_valid_json_after_inlining() -> None:
    import json as _json

    json_text = _json.dumps({"schema_version": "1", "unicode": "safe \u2028 zone"})
    html = render_html(json_text)
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="harness-results")
    # Round-trip: the inlined, escaped form parses back into the same
    # object.
    parsed = _json.loads(script.string)
    assert parsed["unicode"] == "safe \u2028 zone"


# ---------------------------------------------------------------------------
# End-to-end via pytester — user-facing test content cannot inject markup.
# ---------------------------------------------------------------------------


def test_a_test_name_containing_html_should_appear_as_text_only(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_xss="""
        import pytest

        @pytest.mark.parametrize("payload", ["<script>alert(1)</script>"])
        def test_param(payload):
            assert True
        """
    )
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    html = (report_dir / "index.html").read_text(encoding="utf-8")

    # The attacker-controlled parametrize ID must not land as an
    # executable `<script>alert(...)</script>` pair anywhere in the
    # document. Pytest sanitises parametrize IDs for its own collection
    # display, but any `<` characters that do survive must reach the
    # JSON block in escaped form so the browser parser keeps them as
    # data, not markup.
    assert not re.search(r"<script>\s*alert", html)
    soup = BeautifulSoup(html, "html.parser")
    # Only the two bundled script tags: the JSON block and the vanilla
    # JS. No attacker-created third element.
    assert len(soup.find_all("script")) == 2

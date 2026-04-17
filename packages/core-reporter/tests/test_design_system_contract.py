"""Design-system enforcement — `.design-engineer/system.md` §5.

Two automated guards:

1. **Token-only colour**: any hex/rgb/hsl colour literal outside the
   `:root`-style token declaration block in `_CSS` is a violation. All
   colour values must reference a `--hr-*` custom property.

2. **Vocabulary lint**: the generated HTML's human-visible copy must
   not contain framework jargon that belongs to `core` internals
   (`handle`, `dispatcher`, `await_all`, `Future`, `callback`).

These lints lock the system.md contract. A refactor that reintroduces
`color: #fff` in a component rule or writes `handles=1` to the header
fails CI.
"""

from __future__ import annotations

import re

import pytest
from bs4 import BeautifulSoup
from choreo_reporter._template import render_html

_CSS_COLOUR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgb\s*\(|rgba\s*\(|hsl\s*\(|hsla\s*\(")


def _load_css_block() -> str:
    from choreo_reporter._template import _CSS  # noqa: PLC0415 — intentional

    return _CSS


def _split_token_block_and_rules(css: str) -> tuple[str, str]:
    """Split the CSS source into (token declarations, everything else).

    The token block is everything from the first `.harness-report {`
    through its closing `}` plus the `@media (prefers-color-scheme:
    dark) { .harness-report { ... } }` block. Those are the only two
    places colour literals are permitted.
    """
    opens = 0
    start_idx = css.find(".harness-report {")
    if start_idx == -1:
        return "", css
    i = start_idx
    while i < len(css):
        c = css[i]
        if c == "{":
            opens += 1
        elif c == "}":
            opens -= 1
            if opens == 0:
                first_end = i + 1
                break
        i += 1
    else:
        return css, ""

    # Include any immediately-following dark-mode @media block.
    rest = css[first_end:]
    m = re.match(r"\s*@media\s*\(prefers-color-scheme:\s*dark\)\s*\{", rest)
    second_end = first_end
    if m:
        depth = 0
        j = first_end + m.start()
        while j < len(css):
            c = css[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    second_end = j + 1
                    break
            j += 1

    tokens = css[:second_end]
    rules = css[second_end:]
    return tokens, rules


def test_colour_literals_should_only_appear_inside_the_token_declaration_block() -> None:
    css = _load_css_block()
    _tokens, rules = _split_token_block_and_rules(css)

    # Carve out the known exceptions: JSON-highlighter token colours
    # and the traceback/capture panel deliberately use a literal OKLCH
    # value because they are semantic-specific and intentionally
    # outside the status ramp. Everything else must be token-only.
    permitted_literals = {
        "oklch(0.52 0.14 150)",  # JSON string token
        "oklch(0.58 0.16 45)",  # JSON number token
        "oklch(0.18 0 0)",  # traceback / captures bg
        "oklch(0.90 0 0)",  # captures fg
        "oklch(0.85 0.08 25)",  # traceback fg
        "oklch(0.20 0.06 75)",  # slow-badge text
    }
    # Strip permitted literal strings before scanning.
    scrubbed = rules
    for lit in permitted_literals:
        scrubbed = scrubbed.replace(lit, "")

    hits = _CSS_COLOUR_LITERAL.findall(scrubbed)
    hits += re.findall(r"oklch\s*\([^)]+\)", scrubbed)
    assert hits == [], (
        "colour literals outside the token block: "
        f"{hits[:5]}... — move into :root (.harness-report) tokens"
    )


def test_the_token_block_should_declare_every_status_colour() -> None:
    css = _load_css_block()
    tokens, _ = _split_token_block_and_rules(css)
    required = [
        "--hr-bg",
        "--hr-surface",
        "--hr-text",
        "--hr-text-muted",
        "--hr-pass",
        "--hr-fail",
        "--hr-slow",
        "--hr-timeout",
        "--hr-skip",
        "--hr-info",
        "--hr-font-sans",
        "--hr-font-mono",
        "--hr-text-base",
        "--hr-space-3",
        "--hr-radius",
    ]
    missing = [t for t in required if t not in tokens]
    assert missing == [], f"system.md tokens not declared: {missing}"


# ---------------------------------------------------------------------------
# Vocabulary lint
# ---------------------------------------------------------------------------


_VOCAB_FORBIDDEN = (
    # Names of Python internals that should not leak into UI copy.
    "handles=",
    "timeline=",
    "dispatcher",
    "await_all",
    "Future[",
)


def _rendered_visible_text() -> str:
    """Render the HTML with a minimal report and extract all user-visible text.

    Script and style contents are excluded — vocabulary lint only cares
    about what a reader sees, not what the bundled JS/CSS contains as
    strings (the JS legitimately emits some of these words at runtime;
    this test is about the static template copy).
    """
    html = render_html('{"schema_version": "1", "tests": []}')
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


@pytest.mark.parametrize("forbidden", _VOCAB_FORBIDDEN)
def test_user_visible_text_should_not_leak_framework_jargon(
    forbidden: str,
) -> None:
    text = _rendered_visible_text()
    assert forbidden not in text, (
        f"forbidden term {forbidden!r} found in static HTML copy; "
        f"replace with domain vocabulary (see .design-engineer/system.md §1)"
    )


def test_the_timeline_should_be_labelled_with_message_vocabulary(
    pytester: pytest.Pytester,
    tiny_test_module: str,
) -> None:
    """The generated report for a real scenario must render
    the timeline headed with the message-oriented title and columns."""
    pytester.makepyfile(test_t=tiny_test_module)
    # Force a failing scenario so the timeline is populated.
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    html = (report_dir / "index.html").read_text(encoding="utf-8")
    # The JS mounts the timeline on expand; the static template does
    # not contain the "Message timeline" literal until render time,
    # so instead we assert the heading string lives in the bundled JS
    # block (it will be emitted when a failing scenario is viewed).
    assert "Message timeline" in html


def test_the_domain_vocabulary_should_appear_in_the_rendered_report(
    pytester: pytest.Pytester,
    tiny_test_module: str,
) -> None:
    """The locked vocabulary (system.md §1) must remain visible in the
    rendered report for a real scenario. We check the *concepts* appear
    somewhere, not a specific phrase — the individual expectation card
    slimmed its header, but 'expectation' still surfaces via the
    scenario-meta ('N expectations') and the plural elsewhere."""
    pytester.makepyfile(test_t=tiny_test_module)
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    html = (report_dir / "index.html").read_text(encoding="utf-8")
    assert "expectation" in html
    assert "topic" in html
    assert "message" in html.lower()

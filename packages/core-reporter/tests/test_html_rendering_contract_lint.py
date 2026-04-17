"""Rendering-contract lint — PRD-007 §9.

The bundled JavaScript must never use DOM sinks that interpret
payload-derived strings as HTML. This test scans the rendered template
and fails if any forbidden token appears in *executable* JS (comments
are stripped before the scan so a warning note like "never use innerHTML"
does not trip the lint).

A human reviewer is not a reliable guard on future edits, and a
refactor that introduces `.innerHTML = data.foo` would silently
reintroduce the XSS class that §9 exists to prevent. This lint catches
it in CI.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from choreo_reporter._template import FORBIDDEN_JS_SINKS, render_html

_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(js: str) -> str:
    js = _BLOCK_COMMENT.sub("", js)
    js = _LINE_COMMENT.sub("", js)
    return js


def _bundled_js() -> str:
    html = render_html('{"schema_version": "1", "tests": []}')
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script")
    # Two scripts: the JSON block (type=application/json) and the
    # bundled vanilla JS (no type attribute).
    js_scripts = [s for s in scripts if s.get("type") is None]
    assert len(js_scripts) == 1
    return _strip_comments(js_scripts[0].string or "")


def test_the_bundled_js_should_not_use_innerHTML() -> None:
    assert "innerHTML" not in _bundled_js()


def test_the_bundled_js_should_not_use_outerHTML() -> None:
    assert "outerHTML" not in _bundled_js()


def test_the_bundled_js_should_not_use_insertAdjacentHTML() -> None:
    assert "insertAdjacentHTML" not in _bundled_js()


def test_the_bundled_js_should_not_call_document_write() -> None:
    assert "document.write" not in _bundled_js()


def test_the_bundled_js_should_not_call_eval() -> None:
    assert "eval(" not in _bundled_js()


def test_the_bundled_js_should_not_construct_new_function() -> None:
    assert "new Function" not in _bundled_js()


def test_every_forbidden_sink_listed_in_the_module_should_be_absent() -> None:
    """Catch-all: if FORBIDDEN_JS_SINKS grows, the lint grows with it."""
    js = _bundled_js()
    found = [s for s in FORBIDDEN_JS_SINKS if s in js]
    assert found == [], f"forbidden JS sinks present: {found}"

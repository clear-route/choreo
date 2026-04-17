"""Library-level redaction helpers (ADR-0017 §Security Considerations).

Matcher descriptions carry literal values from `field_equals` et al. Any time
such a description crosses a boundary where it could be observed (log line,
report file) it must be redacted — the unredacted text stays on the
in-memory `ReplyReport.matcher_description` so tests can still assert on
the full description.

Two callers today: `choreo.scenario` uses this on the never-fired reply
WARNING log path; `choreo_reporter._serialise` uses it when writing matcher
descriptions into the test report.
"""

from __future__ import annotations

import re

# Order matters: quoted forms are tried before the bareword fallback so a
# quoted value does not bleed past its closing delimiter. Each quoted alt
# honours backslash escapes so `name='it\'s'` (emitted by some reprs that
# force single quotes) redacts as a whole token rather than truncating at
# the escaped apostrophe and leaking the trailing characters.
_MATCHER_LITERAL_RE = re.compile(
    r"=\s*'(?:\\.|[^\\'])*'"  # single-quoted, escape-aware
    r"|=\s*\"(?:\\.|[^\\\"])*\""  # double-quoted, escape-aware
    r"|=\s*[^\s,()]+",  # bareword / number / True
    re.DOTALL,  # match across newlines for multi-line values
)


def redact_matcher_description(desc: str) -> str:
    """Replace every `=value` fragment in a matcher description with
    `=<value>` so literal arguments do not reach logs or serialised reports
    (ADR-0017). The returned string is safe to emit at WARNING.

    The regex is escape-aware for quoted values. Pathologically malformed
    descriptions (mismatched quote nesting — something `repr()` does not
    emit for library-produced strings) may still leak fragments; defence-
    in-depth lives in the report serialiser, which prefers structured
    `expected_shape` over parsing prose when available.
    """
    return _MATCHER_LITERAL_RE.sub("=<value>", desc)

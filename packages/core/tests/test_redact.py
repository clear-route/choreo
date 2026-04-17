"""Unit tests for matcher-description redaction (ADR-0017).

The regex-based redactor is deliberately conservative: it must collapse
every `=value` fragment to `=<value>`, even when the value contains
embedded equals signs, escaped quotes, or newlines. Each case here
guards against a real leak mode the auditor flagged.
"""

from __future__ import annotations


def test_quoted_values_should_redact_to_a_placeholder() -> None:
    from choreo._redact import redact_matcher_description

    assert redact_matcher_description("account='ACME-123'") == "account=<value>"
    assert redact_matcher_description('note="hello"') == "note=<value>"


def test_bareword_values_should_redact_to_a_placeholder() -> None:
    from choreo._redact import redact_matcher_description

    assert redact_matcher_description("qty=1000") == "qty=<value>"
    assert redact_matcher_description("active=True") == "active=<value>"


def test_bareword_with_embedded_equals_should_redact_as_a_whole() -> None:
    """`account=secret=with=equals` must not leak the tail after the first `=`."""
    from choreo._redact import redact_matcher_description

    assert redact_matcher_description("account=secret=with=equals") == "account=<value>"


def test_values_containing_escaped_quotes_should_redact_as_a_whole() -> None:
    """Python's `repr()` on `"it's"` may emit a single-quoted literal with
    a backslash-escaped apostrophe. The redactor must honour the escape so
    it doesn't truncate at the inner quote and leak the tail."""
    from choreo._redact import redact_matcher_description

    # Single-quoted with an escaped apostrophe inside.
    assert redact_matcher_description("name='it\\'s'") == "name=<value>"
    # Double-quoted with escaped inner double-quote.
    assert redact_matcher_description('note="\\"inner\\""') == "note=<value>"


def test_multiline_values_should_redact_as_a_whole() -> None:
    """A matcher description containing a newline inside a quoted value
    must not leak the second line (re.DOTALL on the alternates)."""
    from choreo._redact import redact_matcher_description

    desc = 'multiline="line1\nline2"'
    assert redact_matcher_description(desc) == "multiline=<value>"


def test_multiple_kwargs_should_each_redact_independently() -> None:
    from choreo._redact import redact_matcher_description

    assert (
        redact_matcher_description("field_equals(k=v1, other=v2)")
        == "field_equals(k=<value>, other=<value>)"
    )


def test_nested_matcher_composition_should_redact_every_literal() -> None:
    from choreo._redact import redact_matcher_description

    assert (
        redact_matcher_description("all_of(field_equals(account='ACME-1'), field_equals(qty=100))")
        == "all_of(field_equals(account=<value>), field_equals(qty=<value>))"
    )


def test_descriptions_with_no_equals_should_pass_through() -> None:
    from choreo._redact import redact_matcher_description

    assert redact_matcher_description("(any)") == "(any)"
    assert redact_matcher_description("not_(field_exists(account))") == (
        "not_(field_exists(account))"
    )

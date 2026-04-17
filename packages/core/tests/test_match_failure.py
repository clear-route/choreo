"""Structured `MatchFailure` coverage — every built-in matcher populates it.

The free-form `MatchResult.reason` string is on its way out (design doc: stop
leaking `expected 'PASS', got 'FAIL'` prose into reports). These tests assert
that every matcher emits a typed `MatchFailure` with the expected kind, path,
and structured expected/actual data — the forms the report UI will render from
once the reason string is removed.
"""
from __future__ import annotations


def test_field_equals_should_emit_a_mismatch_failure_when_values_differ() -> None:
    from core.matchers import MatchFailure, field_equals

    result = field_equals("status", "PASS").match({"status": "FAIL"})
    assert result.matched is False
    assert result.failure == MatchFailure(
        kind="mismatch", path="status", expected="PASS", actual="FAIL"
    )


def test_field_equals_should_emit_a_missing_failure_when_the_field_is_absent() -> None:
    from core.matchers import MatchFailure, field_equals

    result = field_equals("status", "PASS").match({})
    assert result.matched is False
    assert result.failure == MatchFailure(
        kind="missing", path="status", expected="PASS"
    )


def test_field_equals_should_not_emit_a_failure_when_matched() -> None:
    from core.matchers import field_equals

    result = field_equals("status", "PASS").match({"status": "PASS"})
    assert result.matched is True
    assert result.failure is None


def test_field_in_should_emit_a_predicate_failure_when_value_is_outside_the_set() -> None:
    from core.matchers import MatchFailure, field_in

    result = field_in("reason", ("CREDIT", "LIMIT")).match({"reason": "OTHER"})
    assert result.failure == MatchFailure(
        kind="predicate",
        path="reason",
        expected={"in": ["CREDIT", "LIMIT"]},
        actual="OTHER",
    )


def test_field_gt_should_emit_a_predicate_failure_when_value_is_not_greater() -> None:
    from core.matchers import MatchFailure, field_gt

    result = field_gt("qty", 100).match({"qty": 99})
    assert result.failure == MatchFailure(
        kind="predicate", path="qty", expected={"gt": 100}, actual=99
    )


def test_field_gt_should_emit_a_type_mismatch_failure_when_values_are_incomparable() -> None:
    from core.matchers import MatchFailure, field_gt

    result = field_gt("qty", 100).match({"qty": "not-a-number"})
    assert result.failure == MatchFailure(
        kind="type_mismatch",
        path="qty",
        expected={"gt": 100},
        actual="not-a-number",
    )


def test_field_lt_should_emit_a_predicate_failure_when_value_is_not_less() -> None:
    from core.matchers import MatchFailure, field_lt

    result = field_lt("qty", 100).match({"qty": 100})
    assert result.failure == MatchFailure(
        kind="predicate", path="qty", expected={"lt": 100}, actual=100
    )


def test_field_exists_should_emit_a_missing_failure_when_the_key_is_absent() -> None:
    from core.matchers import MatchFailure, field_exists

    result = field_exists("booking_id").match({})
    assert result.failure == MatchFailure(
        kind="missing", path="booking_id", expected={"exists": True}
    )


def test_payload_contains_should_emit_a_predicate_failure_when_substring_is_absent() -> None:
    from core.matchers import MatchFailure, payload_contains

    # R5 normalisation: `actual` carries the raw payload bytes directly (every
    # other matcher's `actual` is a scalar); reporter hex-encodes for display.
    result = payload_contains(b"MAGIC-HDR").match(b"hello")
    assert result.failure == MatchFailure(
        kind="predicate",
        path="<raw>",
        expected={"payload_contains_hex": b"MAGIC-HDR".hex()},
        actual=b"hello",
    )


def test_eq_should_emit_a_mismatch_failure_against_the_whole_payload() -> None:
    from core.matchers import MatchFailure, eq

    result = eq(42).match(7)
    assert result.failure == MatchFailure(
        kind="mismatch", path="<root>", expected=42, actual=7
    )


def test_in_should_emit_a_predicate_failure_when_value_is_outside_the_set() -> None:
    from core.matchers import MatchFailure, in_

    result = in_(("A", "B")).match("C")
    assert result.failure == MatchFailure(
        kind="predicate", path="<root>", expected={"in": ["A", "B"]}, actual="C"
    )


def test_gt_should_emit_a_type_mismatch_failure_for_incomparable_payloads() -> None:
    from core.matchers import MatchFailure, gt

    result = gt(10).match("x")
    assert result.failure == MatchFailure(
        kind="type_mismatch", path="<root>", expected={"gt": 10}, actual="x"
    )


def test_all_of_should_emit_a_composed_failure_carrying_the_first_failing_child() -> None:
    from core.matchers import MatchFailure, all_of, field_equals

    result = all_of(
        field_equals("a", 1), field_equals("b", 2)
    ).match({"a": 1, "b": 99})
    assert result.failure is not None
    assert result.failure.kind == "composed"
    assert result.failure.path == "all_of"
    assert len(result.failure.children) == 1
    assert result.failure.children[0] == MatchFailure(
        kind="mismatch", path="b", expected=2, actual=99
    )


def test_any_of_should_emit_a_composed_failure_carrying_every_child() -> None:
    from core.matchers import MatchFailure, any_of, field_equals

    result = any_of(
        field_equals("a", 1), field_equals("a", 2)
    ).match({"a": 99})
    assert result.failure is not None
    assert result.failure.kind == "composed"
    assert result.failure.path == "any_of"
    assert len(result.failure.children) == 2
    assert result.failure.children[0] == MatchFailure(
        kind="mismatch", path="a", expected=1, actual=99
    )
    assert result.failure.children[1] == MatchFailure(
        kind="mismatch", path="a", expected=2, actual=99
    )


def test_not_should_emit_a_predicate_failure_when_the_inner_matches() -> None:
    from core.matchers import MatchFailure, field_equals, not_

    result = not_(field_equals("status", "PASS")).match({"status": "PASS"})
    assert result.failure == MatchFailure(
        kind="predicate",
        path="<root>",
        expected={"not": {"status": "PASS"}},
        actual={"status": "PASS"},
    )


def test_contains_fields_should_emit_a_mismatch_failure_with_the_walked_path() -> None:
    from core.matchers import MatchFailure, contains_fields

    result = contains_fields({"item": {"status": "COMPLETED"}}).match(
        {"item": {"status": "PART"}}
    )
    assert result.failure == MatchFailure(
        kind="mismatch", path="item.status", expected="COMPLETED", actual="PART"
    )


def test_contains_fields_should_emit_a_missing_failure_when_a_spec_key_is_absent() -> None:
    from core.matchers import MatchFailure, contains_fields

    result = contains_fields({"item": {"status": "COMPLETED"}}).match({"item": {}})
    assert result.failure == MatchFailure(
        kind="missing", path="item.status", expected="COMPLETED"
    )


def test_contains_fields_should_reroot_an_embedded_matchers_failure_under_the_walked_path() -> None:
    from core.matchers import MatchFailure, contains_fields, gt

    result = contains_fields({"item": {"qty": gt(0)}}).match(
        {"item": {"qty": -1}}
    )
    assert result.failure == MatchFailure(
        kind="predicate",
        path="item.qty",
        expected={"gt": 0},
        actual=-1,
    )


def test_contains_fields_should_emit_a_type_mismatch_failure_when_payload_is_wrong_shape() -> None:
    from core.matchers import MatchFailure, contains_fields

    result = contains_fields({"item": {"status": "COMPLETED"}}).match(
        {"item": "not-a-dict"}
    )
    assert result.failure == MatchFailure(
        kind="type_mismatch",
        path="item",
        expected={"type": "dict"},
        actual="str",
    )


# ---------------------------------------------------------------------------
# Nested custom-matcher rerooting — review finding C2
#
# A custom matcher that itself emits a `composed` failure with its own
# children must have those children rerooted under the walked path. Today
# only the outermost failure is patched, so reporter output misrenders for
# any user matcher more complex than a leaf predicate.
# ---------------------------------------------------------------------------


def test_contains_fields_should_reroot_children_of_a_composed_failure_from_a_user_matcher() -> None:
    from dataclasses import dataclass, field

    from core.matchers import MatchFailure, MatchResult, contains_fields

    @dataclass(frozen=True)
    class UserComposite:
        description: str = "user_composite"

        def match(self, payload: object) -> MatchResult:
            # Two child failures, both reporting themselves at the default
            # <root> because a matcher has no idea where it was embedded.
            children = (
                MatchFailure(
                    kind="predicate",
                    path="<root>",
                    expected={"gt": 0},
                    actual=payload,
                ),
                MatchFailure(
                    kind="predicate",
                    path="<root>",
                    expected={"lt": 1000},
                    actual=payload,
                ),
            )
            return MatchResult(
                matched=False,
                reason="user_composite failed",
                failure=MatchFailure(
                    kind="composed",
                    path="all_of",
                    expected="user_composite(...)",
                    children=children,
                ),
            )

    result = contains_fields({"item": {"qty": UserComposite()}}).match(
        {"item": {"qty": -1}}
    )
    assert result.matched is False
    assert result.failure is not None
    # Every child that said "<root>" must now carry the walked path so the
    # report does not render two misleading "<root>" paths for one failure.
    for child in result.failure.children:
        assert child.path == "item.qty", (
            f"expected child to be rerooted under item.qty, got {child.path!r}"
        )


def test_contains_fields_should_reroot_a_user_matcher_whose_outer_path_is_not_root() -> None:
    """The reroot helper today only patches `path == '<root>'`. A user matcher
    that returns a composed failure with its own outer path (e.g. 'all_of')
    also needs to be rerooted, otherwise the report shows 'all_of' in place
    of the actual walked position."""
    from dataclasses import dataclass

    from core.matchers import MatchFailure, MatchResult, contains_fields

    @dataclass(frozen=True)
    class UserCompositeWithOwnPath:
        description: str = "user_composite_own_path"

        def match(self, payload: object) -> MatchResult:
            return MatchResult(
                matched=False,
                reason="outer not root",
                failure=MatchFailure(
                    kind="composed",
                    # Not "<root>" — the matcher declares its own scope.
                    path="all_of",
                    expected="composite",
                    children=(),
                ),
            )

    result = contains_fields({"item": {"qty": UserCompositeWithOwnPath()}}).match(
        {"item": {"qty": 1}}
    )
    assert result.failure is not None
    # The walked position must appear somewhere the reporter can find it.
    # Either the outer path is rerooted, or a MatchContext-style carrier
    # surfaces the walked position alongside the matcher's own scope label.
    # For this behavioural test we assert the former: the outer path is the
    # walked path.
    assert result.failure.path == "item.qty"


# ---------------------------------------------------------------------------
# _expected_shape should log when a user's expected_shape() raises.
#
# Silent except-Exception (today's behaviour) hides bugs in framework-internal
# matchers that would otherwise be caught by scenario-log inspection during
# development. Keep the tolerant envelope, but emit a DEBUG record.
# ---------------------------------------------------------------------------


def test_expected_shape_should_emit_a_debug_log_when_the_user_hook_raises(caplog) -> None:
    import logging
    from dataclasses import dataclass

    from core.matchers import MatchResult, _expected_shape

    @dataclass
    class Buggy:
        description: str = "buggy"

        def match(self, payload):
            return MatchResult(True, "ok")

        def expected_shape(self):
            raise RuntimeError("kaboom")

    with caplog.at_level(logging.DEBUG, logger="core.matchers"):
        assert _expected_shape(Buggy()) is None

    # Some record names the matcher and the exception type. We do not assert
    # a specific format string; the record just has to be there so the log is
    # a rediscoverable breadcrumb when a reporter mysteriously falls back to
    # `description`.
    assert any(
        "expected_shape" in r.message and "kaboom" in r.message
        for r in caplog.records
    ), f"no log record from _expected_shape; got: {[r.message for r in caplog.records]}"

"""Behavioural tests for Matcher Strategy (ADR-0013).

Each matcher is a pure predicate over a decoded payload. Failure results carry
a human-readable reason. `all_of` / `any_of` / `not_` compose them into boolean
expressions.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# field_equals
# ---------------------------------------------------------------------------


def test_field_equals_should_match_when_the_field_value_is_exactly_equal() -> None:
    from choreo.matchers import field_equals

    m = field_equals("status", "PASS")
    assert m.match({"status": "PASS"}).matched is True


def test_field_equals_should_not_match_when_the_field_value_differs() -> None:
    from choreo.matchers import field_equals

    m = field_equals("status", "PASS")
    result = m.match({"status": "FAIL"})
    assert result.matched is False


def test_field_equals_should_not_match_when_the_field_is_missing() -> None:
    from choreo.matchers import field_equals

    m = field_equals("status", "PASS")
    assert m.match({}).matched is False


def test_field_equals_failure_reason_should_name_expected_and_actual() -> None:
    from choreo.matchers import field_equals

    m = field_equals("status", "PASS")
    result = m.match({"status": "FAIL"})
    assert "PASS" in result.reason
    assert "FAIL" in result.reason


# ---------------------------------------------------------------------------
# field_in
# ---------------------------------------------------------------------------


def test_field_in_should_match_when_the_value_is_a_member_of_the_set() -> None:
    from choreo.matchers import field_in

    m = field_in("reason", ("CREDIT", "LIMIT"))
    assert m.match({"reason": "LIMIT"}).matched is True


def test_field_in_should_not_match_when_the_value_is_not_a_member() -> None:
    from choreo.matchers import field_in

    m = field_in("reason", ("CREDIT", "LIMIT"))
    assert m.match({"reason": "OTHER"}).matched is False


# ---------------------------------------------------------------------------
# field_gt / field_lt
# ---------------------------------------------------------------------------


def test_field_gt_should_match_when_value_is_strictly_greater() -> None:
    from choreo.matchers import field_gt

    m = field_gt("qty", 100)
    assert m.match({"qty": 101}).matched is True
    assert m.match({"qty": 100}).matched is False


def test_field_lt_should_match_when_value_is_strictly_less() -> None:
    from choreo.matchers import field_lt

    m = field_lt("qty", 100)
    assert m.match({"qty": 99}).matched is True
    assert m.match({"qty": 100}).matched is False


# ---------------------------------------------------------------------------
# field_exists
# ---------------------------------------------------------------------------


def test_field_exists_should_match_when_the_key_is_present() -> None:
    from choreo.matchers import field_exists

    m = field_exists("booking_id")
    assert m.match({"booking_id": "abc"}).matched is True


def test_field_exists_should_not_match_when_the_key_is_absent() -> None:
    from choreo.matchers import field_exists

    m = field_exists("booking_id")
    assert m.match({}).matched is False


# ---------------------------------------------------------------------------
# payload_contains
# ---------------------------------------------------------------------------


def test_payload_contains_should_match_when_substring_present_in_bytes() -> None:
    from choreo.matchers import payload_contains

    m = payload_contains(b"MAGIC-HDR")
    assert m.match(b"8=MAGIC-HDR\x019=100\x01").matched is True


def test_payload_contains_should_not_match_when_substring_absent() -> None:
    from choreo.matchers import payload_contains

    m = payload_contains(b"MAGIC-HDR")
    assert m.match(b"hello world").matched is False


# ---------------------------------------------------------------------------
# all_of
# ---------------------------------------------------------------------------


def test_all_of_should_match_only_when_every_child_matches() -> None:
    from choreo.matchers import all_of, field_equals, field_gt

    m = all_of(field_equals("status", "PASS"), field_gt("qty", 0))
    assert m.match({"status": "PASS", "qty": 1}).matched is True


def test_all_of_should_not_match_when_any_child_fails() -> None:
    from choreo.matchers import all_of, field_equals, field_gt

    m = all_of(field_equals("status", "PASS"), field_gt("qty", 0))
    assert m.match({"status": "PASS", "qty": 0}).matched is False
    assert m.match({"status": "FAIL", "qty": 1}).matched is False


# ---------------------------------------------------------------------------
# any_of
# ---------------------------------------------------------------------------


def test_any_of_should_match_when_at_least_one_child_matches() -> None:
    from choreo.matchers import any_of, field_equals

    m = any_of(field_equals("status", "PASS"), field_equals("status", "OK"))
    assert m.match({"status": "OK"}).matched is True


def test_any_of_should_not_match_when_every_child_fails() -> None:
    from choreo.matchers import any_of, field_equals

    m = any_of(field_equals("status", "PASS"), field_equals("status", "OK"))
    assert m.match({"status": "NO"}).matched is False


# ---------------------------------------------------------------------------
# not_
# ---------------------------------------------------------------------------


def test_not_should_invert_the_childs_result() -> None:
    from choreo.matchers import field_equals, not_

    m = not_(field_equals("status", "FAIL"))
    assert m.match({"status": "PASS"}).matched is True
    assert m.match({"status": "FAIL"}).matched is False


# ---------------------------------------------------------------------------
# Composition + description
# ---------------------------------------------------------------------------


def test_a_composed_matcher_should_report_its_composition_in_its_description() -> None:
    from choreo.matchers import all_of, field_equals, field_gt

    m = all_of(field_equals("status", "PASS"), field_gt("qty", 0))
    assert "status" in m.description
    assert "qty" in m.description


def test_matchers_should_be_hashable_and_reusable_across_scenarios() -> None:
    """Matchers carry no mutable state; one instance can be used by many scenarios."""
    from choreo.matchers import field_equals

    m = field_equals("status", "PASS")
    # Two matches, different payloads, independent results.
    r1 = m.match({"status": "PASS"})
    r2 = m.match({"status": "FAIL"})
    assert r1.matched is True
    assert r2.matched is False


# ---------------------------------------------------------------------------
# Deep path traversal — list indices and nested dicts
# ---------------------------------------------------------------------------


def test_field_equals_should_traverse_into_a_nested_dict() -> None:
    from choreo.matchers import field_equals

    m = field_equals("item.status", "COMPLETED")
    payload = {"item": {"status": "COMPLETED", "qty": 100}}
    assert m.match(payload).matched is True


def test_field_equals_should_traverse_into_a_list_index() -> None:
    from choreo.matchers import field_equals

    m = field_equals("entries.0.price", 100.25)
    payload = {"entries": [{"price": 100.25, "qty": 500}, {"price": 100.50}]}
    assert m.match(payload).matched is True


def test_field_equals_should_traverse_mixed_dict_and_list_nesting() -> None:
    from choreo.matchers import field_equals

    m = field_equals("item.entries.1.qty", 250)
    payload = {
        "item": {
            "entries": [
                {"qty": 500, "amount": 100.25},
                {"qty": 250, "amount": 100.50},
            ],
        }
    }
    assert m.match(payload).matched is True


def test_field_equals_should_not_match_when_a_list_index_is_out_of_range() -> None:
    from choreo.matchers import field_equals

    m = field_equals("entries.5.price", 100.25)
    assert m.match({"entries": [{"price": 100.25}]}).matched is False


def test_field_exists_should_traverse_nested_paths() -> None:
    from choreo.matchers import field_exists

    m = field_exists("item.booking_id")
    assert m.match({"item": {"booking_id": "abc"}}).matched is True
    assert m.match({"item": {}}).matched is False


# ---------------------------------------------------------------------------
# contains_fields — recursive subset match for nested structures
# ---------------------------------------------------------------------------


def test_contains_fields_should_match_when_every_spec_key_is_present_with_the_same_value() -> None:
    from choreo.matchers import contains_fields

    m = contains_fields({"status": "COMPLETED"})
    assert m.match({"status": "COMPLETED", "qty": 100, "other": "x"}).matched is True


def test_contains_fields_should_match_nested_dicts_recursively() -> None:
    from choreo.matchers import contains_fields

    m = contains_fields({"item": {"status": "COMPLETED"}})
    payload = {
        "item": {"status": "COMPLETED", "qty": 100, "last_amount": 100.25},
        "actor": "alice",
    }
    assert m.match(payload).matched is True


def test_contains_fields_should_not_match_when_a_nested_value_differs() -> None:
    from choreo.matchers import contains_fields

    m = contains_fields({"item": {"status": "COMPLETED"}})
    assert m.match({"item": {"status": "REJECTED"}}).matched is False


def test_contains_fields_should_not_match_when_a_spec_key_is_missing() -> None:
    from choreo.matchers import contains_fields

    m = contains_fields({"item": {"status": "COMPLETED"}})
    assert m.match({"actor": "alice"}).matched is False


def test_contains_fields_should_match_list_items_positionally() -> None:
    from choreo.matchers import contains_fields

    m = contains_fields({"entries": [{"amount": 100.25}]})
    payload = {"entries": [{"amount": 100.25, "qty": 500}, {"amount": 100.50, "qty": 250}]}
    assert m.match(payload).matched is True


def test_contains_fields_should_not_match_when_a_list_is_shorter_than_the_spec() -> None:
    from choreo.matchers import contains_fields

    m = contains_fields({"entries": [{"amount": 100.25}, {"amount": 100.50}]})
    assert m.match({"entries": [{"amount": 100.25}]}).matched is False


def test_contains_fields_failure_reason_should_name_the_first_mismatch() -> None:
    from choreo.matchers import contains_fields

    m = contains_fields({"item": {"status": "COMPLETED"}})
    result = m.match({"item": {"status": "REJECTED"}})
    # The reason names the nested path that mismatched.
    assert "status" in result.reason
    assert "REJECTED" in result.reason


# ---------------------------------------------------------------------------
# Pathless value matchers — eq / in_ / gt / lt
# ---------------------------------------------------------------------------


def test_eq_should_match_when_the_payload_equals_the_value() -> None:
    from choreo.matchers import eq

    assert eq(100).match(100).matched is True


def test_eq_should_not_match_when_the_payload_differs() -> None:
    from choreo.matchers import eq

    r = eq(100).match(99)
    assert r.matched is False
    assert "100" in r.reason and "99" in r.reason


def test_in_should_match_when_the_payload_is_a_member() -> None:
    from choreo.matchers import in_

    assert in_(("NEW", "PART", "DONE")).match("PART").matched is True


def test_in_should_not_match_when_the_payload_is_not_a_member() -> None:
    from choreo.matchers import in_

    assert in_(("NEW", "PART")).match("REJECTED").matched is False


def test_gt_should_match_when_the_payload_is_greater() -> None:
    from choreo.matchers import gt

    assert gt(0).match(1).matched is True


def test_gt_should_not_match_when_the_payload_is_equal_or_less() -> None:
    from choreo.matchers import gt

    assert gt(0).match(0).matched is False
    assert gt(0).match(-1).matched is False


def test_gt_should_report_a_reason_when_types_cannot_be_compared() -> None:
    from choreo.matchers import gt

    r = gt(0).match("not a number")
    assert r.matched is False
    assert "cannot compare" in r.reason


def test_lt_should_match_when_the_payload_is_less() -> None:
    from choreo.matchers import lt

    assert lt(10).match(5).matched is True


def test_lt_should_not_match_when_the_payload_is_equal_or_greater() -> None:
    from choreo.matchers import lt

    assert lt(10).match(10).matched is False
    assert lt(10).match(11).matched is False


# ---------------------------------------------------------------------------
# contains_fields with embedded matchers — deep validation
# ---------------------------------------------------------------------------


def test_contains_fields_should_apply_a_matcher_embedded_in_the_spec() -> None:
    from choreo.matchers import contains_fields, gt

    m = contains_fields({"item": {"qty": gt(0)}})
    assert m.match({"item": {"qty": 100}}).matched is True


def test_contains_fields_should_not_match_when_an_embedded_matcher_rejects_the_value() -> None:
    from choreo.matchers import contains_fields, gt

    m = contains_fields({"item": {"qty": gt(0)}})
    r = m.match({"item": {"qty": 0}})
    assert r.matched is False
    # The failure path names the nested position, and the reason carries the
    # embedded matcher's own failure text.
    assert "item.qty" in r.reason
    assert "> 0" in r.reason


def test_contains_fields_should_apply_a_matcher_at_a_list_index() -> None:
    from choreo.matchers import contains_fields, gt

    m = contains_fields({"entries": [{"amount": gt(100.0)}]})
    assert m.match({"entries": [{"amount": 100.25, "qty": 500}]}).matched is True


def test_contains_fields_should_report_the_list_index_when_an_embedded_matcher_fails() -> None:
    from choreo.matchers import contains_fields, gt

    m = contains_fields({"entries": [{"amount": gt(100.0)}]})
    r = m.match({"entries": [{"amount": 99.5}]})
    assert r.matched is False
    assert "entries.0.amount" in r.reason


def test_contains_fields_should_mix_literal_and_matcher_siblings() -> None:
    from choreo.matchers import contains_fields, in_

    m = contains_fields(
        {
            "item": {
                "status": "COMPLETED",
                "state": in_(("NEW", "PART", "DONE")),
            },
        }
    )
    payload = {"item": {"status": "COMPLETED", "state": "PART", "qty": 100}}
    assert m.match(payload).matched is True


def test_contains_fields_should_fail_on_the_literal_sibling_when_the_matcher_sibling_passes() -> (
    None
):
    from choreo.matchers import contains_fields, in_

    m = contains_fields(
        {
            "item": {
                "status": "COMPLETED",
                "state": in_(("NEW", "PART", "DONE")),
            },
        }
    )
    payload = {"item": {"status": "REJECTED", "state": "PART"}}
    r = m.match(payload)
    assert r.matched is False
    assert "item.status" in r.reason


def test_contains_fields_should_compose_all_of_as_an_embedded_matcher() -> None:
    from choreo.matchers import all_of, contains_fields, gt, lt

    m = contains_fields({"item": {"qty": all_of(gt(0), lt(1_000_000))}})
    assert m.match({"item": {"qty": 100}}).matched is True
    assert m.match({"item": {"qty": 0}}).matched is False
    assert m.match({"item": {"qty": 10_000_000}}).matched is False


def test_contains_fields_should_accept_a_user_defined_matcher_class() -> None:
    from dataclasses import dataclass

    from choreo.matchers import MatchResult, contains_fields

    @dataclass(frozen=True)
    class IsEven:
        description: str = "is_even"

        def match(self, payload: object) -> MatchResult:
            if isinstance(payload, int) and payload % 2 == 0:
                return MatchResult(True, f"{payload} is even")
            return MatchResult(False, f"{payload!r} is not even")

    m = contains_fields({"item": {"qty": IsEven()}})
    assert m.match({"item": {"qty": 100}}).matched is True
    assert m.match({"item": {"qty": 101}}).matched is False


def test_contains_fields_description_should_use_an_embedded_matchers_description() -> None:
    from choreo.matchers import contains_fields, gt

    m = contains_fields({"qty": gt(0)})
    # The embedded matcher's .description bubbles up into the outer one,
    # rather than the dataclass repr.
    assert "> 0" in m.description


# ---------------------------------------------------------------------------
# expected_shape() — machine-readable form for the test-report writer (PRD-007)
# ---------------------------------------------------------------------------


def test_field_equals_expected_shape_should_describe_path_and_value() -> None:
    from choreo.matchers import field_equals

    m = field_equals("status", "PASS")
    assert m.expected_shape() == {"status": "PASS"}


def test_field_in_expected_shape_should_describe_allowed_values() -> None:
    from choreo.matchers import field_in

    m = field_in("status", ["PASS", "FAIL"])
    assert m.expected_shape() == {"status": {"in": ["PASS", "FAIL"]}}


def test_field_exists_expected_shape_should_mark_existence() -> None:
    from choreo.matchers import field_exists

    m = field_exists("entityId")
    assert m.expected_shape() == {"entityId": {"exists": True}}


def test_contains_fields_expected_shape_should_return_the_nested_spec() -> None:
    from choreo.matchers import contains_fields

    spec = {"item": {"qty": 1000, "side": "UP"}}
    m = contains_fields(spec)
    assert m.expected_shape() == spec


def test_all_of_expected_shape_should_compose_children() -> None:
    from choreo.matchers import all_of, field_equals, field_exists

    m = all_of(field_equals("k", "v"), field_exists("id"))
    assert m.expected_shape() == {"all_of": [{"k": "v"}, {"id": {"exists": True}}]}


def test_any_of_expected_shape_should_compose_children() -> None:
    from choreo.matchers import any_of, field_equals

    m = any_of(field_equals("k", "v1"), field_equals("k", "v2"))
    assert m.expected_shape() == {"any_of": [{"k": "v1"}, {"k": "v2"}]}


def test_not_expected_shape_should_wrap_the_inner_matcher() -> None:
    from choreo.matchers import field_equals, not_

    m = not_(field_equals("k", "v"))
    assert m.expected_shape() == {"not": {"k": "v"}}


def test_payload_contains_expected_shape_should_return_hex() -> None:
    from choreo.matchers import payload_contains

    m = payload_contains(b"\x01\x02")
    assert m.expected_shape() == {"payload_contains_hex": "0102"}


def test_a_matcher_without_expected_shape_should_return_none_from_safe_accessor() -> None:
    """Custom matchers written before the protocol extension (no
    `expected_shape` attribute) must not break the runtime — the safe
    accessor returns None so the reporter falls back to `description`."""
    from dataclasses import dataclass

    from choreo.matchers import MatchResult, _expected_shape

    @dataclass
    class LegacyMatcher:
        description: str = "legacy"

        def match(self, payload):
            return MatchResult(True, "ok")

    assert _expected_shape(LegacyMatcher()) is None


def test_a_matcher_whose_expected_shape_raises_should_return_none() -> None:
    from dataclasses import dataclass

    from choreo.matchers import MatchResult, _expected_shape

    @dataclass
    class BuggyMatcher:
        description: str = "buggy"

        def match(self, payload):
            return MatchResult(True, "ok")

        def expected_shape(self):
            raise RuntimeError("boom")

    assert _expected_shape(BuggyMatcher()) is None


# ---------------------------------------------------------------------------
# Path traversal edge cases — review finding C1
#
# A dotted-string path is sugar for the common case. Real payloads
# (OpenTelemetry, HTTP headers, some binary encodings) have keys that
# literally contain ".". Accept a sequence path to reach them without an
# escape syntax.
# ---------------------------------------------------------------------------


def test_field_equals_should_accept_a_tuple_path_for_a_key_that_contains_a_dot() -> None:
    from choreo.matchers import field_equals

    m = field_equals(("trace.id",), "abc")
    assert m.match({"trace.id": "abc"}).matched is True


def test_field_equals_should_not_match_a_dotted_key_via_dotted_string_sugar() -> None:
    """The dotted-string form is a split-on-dot shortcut. A literal dotted key
    is unreachable that way; callers must use the tuple form."""
    from choreo.matchers import field_equals

    m = field_equals("trace.id", "abc")
    assert m.match({"trace.id": "abc"}).matched is False


def test_field_equals_should_accept_a_tuple_path_mixing_strings_and_list_indices() -> None:
    from choreo.matchers import field_equals

    m = field_equals(("entries", 0, "price"), 100.25)
    payload = {"entries": [{"price": 100.25, "qty": 500}]}
    assert m.match(payload).matched is True


def test_field_equals_should_distinguish_a_numeric_string_key_from_a_list_index() -> None:
    """Tuple form disambiguates: ("items", "0") is a string key; ("items", 0)
    is a list index. Dotted-string cannot express this distinction."""
    from choreo.matchers import field_equals

    m_key = field_equals(("items", "0"), "x")
    m_idx = field_equals(("items", 0), "x")
    as_dict = {"items": {"0": "x"}}
    as_list = {"items": ["x"]}
    assert m_key.match(as_dict).matched is True
    assert m_idx.match(as_list).matched is True
    assert m_key.match(as_list).matched is False
    assert m_idx.match(as_dict).matched is False


def test_field_exists_should_accept_a_tuple_path() -> None:
    from choreo.matchers import field_exists

    m = field_exists(("item", "booking.id"))
    assert m.match({"item": {"booking.id": "abc"}}).matched is True
    assert m.match({"item": {}}).matched is False


def test_contains_fields_failure_path_should_render_a_tuple_path_joined_by_dots() -> None:
    """When contains_fields emits a MatchFailure, its `path` field must still
    be a human-readable dot-joined string even for keys that contain dots;
    tuple-path matchers are the exception that proves the rule."""
    from choreo.matchers import contains_fields

    r = contains_fields({"item": {"qty": 100}}).match({"item": {"qty": 1}})
    assert r.matched is False
    assert r.failure is not None
    assert r.failure.path == "item.qty"


# ---------------------------------------------------------------------------
# Missing primitives — review finding CL2 / CL4
#
# The Scenario DSL's expressive cliff: flat field access vs full document
# shape match with no rung between. Ship the named primitives so users
# don't reach for lambdas (which emit unstructured failures).
# ---------------------------------------------------------------------------


def test_every_should_match_when_every_list_element_passes_the_inner_matcher() -> None:
    from choreo.matchers import every, gt

    m = every(gt(0))
    assert m.match([1, 2, 3]).matched is True


def test_every_should_not_match_when_any_list_element_fails() -> None:
    from choreo.matchers import every, gt

    m = every(gt(0))
    r = m.match([1, 0, 3])
    assert r.matched is False


def test_every_should_not_match_a_non_list_payload() -> None:
    from choreo.matchers import every, gt

    m = every(gt(0))
    assert m.match("not a list").matched is False


def test_any_element_should_match_when_at_least_one_list_element_passes() -> None:
    from choreo.matchers import any_element, field_equals

    m = any_element(field_equals("side", "UP"))
    payload = [{"side": "DOWN"}, {"side": "UP"}, {"side": "DOWN"}]
    assert m.match(payload).matched is True


def test_any_element_should_not_match_when_no_list_element_passes() -> None:
    from choreo.matchers import any_element, field_equals

    m = any_element(field_equals("side", "UP"))
    payload = [{"side": "DOWN"}, {"side": "DOWN"}]
    assert m.match(payload).matched is False


def test_any_element_should_not_match_a_non_list_payload() -> None:
    from choreo.matchers import any_element, field_equals

    m = any_element(field_equals("side", "UP"))
    assert m.match({"side": "UP"}).matched is False


def test_field_ne_should_match_when_the_value_differs() -> None:
    from choreo.matchers import field_ne

    m = field_ne("status", "REJECTED")
    assert m.match({"status": "COMPLETED"}).matched is True


def test_field_ne_should_not_match_when_the_value_is_equal() -> None:
    from choreo.matchers import field_ne

    m = field_ne("status", "REJECTED")
    assert m.match({"status": "REJECTED"}).matched is False


def test_field_ne_should_not_match_when_the_field_is_missing() -> None:
    """Absent-field semantics for a not-equal predicate: no value means we
    cannot verify the predicate, so it does not match (same shape as the
    other field_* matchers on missing keys)."""
    from choreo.matchers import field_ne

    m = field_ne("status", "REJECTED")
    assert m.match({}).matched is False


def test_matches_should_accept_a_regex_pattern_against_a_string_payload() -> None:
    from choreo.matchers import matches

    m = matches(r"^ORD-\d+$")
    assert m.match("ORD-12345").matched is True
    assert m.match("XYZ-12345").matched is False


def test_matches_should_not_match_a_non_string_payload() -> None:
    from choreo.matchers import matches

    m = matches(r"^\d+$")
    assert m.match(12345).matched is False


def test_exists_should_match_any_non_missing_payload_when_used_pathlessly() -> None:
    """Pathless counterpart to field_exists, for use as a leaf matcher inside
    contains_fields: `{"booking_id": exists()}` asserts the key is present
    without constraining its value."""
    from choreo.matchers import contains_fields, exists

    m = contains_fields({"booking_id": exists()})
    assert m.match({"booking_id": "abc", "qty": 1}).matched is True
    assert m.match({"booking_id": None}).matched is True
    assert m.match({"qty": 1}).matched is False


def test_every_and_any_element_should_compose_inside_contains_fields() -> None:
    """The whole point of shipping these: they turn the planned 'there exists
    an entry with amount > 100' assertion into one line inside a shape match."""
    from choreo.matchers import any_element, contains_fields, field_gt

    m = contains_fields({"item": {"entries": any_element(field_gt("amount", 100.0))}})
    payload = {
        "item": {
            "entries": [
                {"amount": 99.0, "qty": 100},
                {"amount": 100.25, "qty": 200},
            ]
        }
    }
    assert m.match(payload).matched is True


# ---------------------------------------------------------------------------
# payload_contains input safety — review finding P4
#
# payload_contains is a raw-bytes escape hatch. Implicitly coercing arbitrary
# payloads via str(payload).encode() is an O(n) foot-gun on large dicts and
# masks the caller's mistake. Require bytes.
# ---------------------------------------------------------------------------


def test_payload_contains_should_reject_non_bytes_payloads_with_type_error() -> None:
    from choreo.matchers import payload_contains

    m = payload_contains(b"MAGIC-HDR")
    with pytest.raises(TypeError):
        m.match({"some": "dict"})


def test_payload_contains_should_reject_a_str_payload() -> None:
    """Strings are not bytes. If a caller has a decoded string they're past
    the layer payload_contains is meant to serve."""
    from choreo.matchers import payload_contains

    m = payload_contains(b"MAGIC-HDR")
    with pytest.raises(TypeError):
        m.match("8=MAGIC-HDR|9=100|")

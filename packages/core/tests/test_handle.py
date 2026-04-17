"""Behavioural tests for Handle (ADR-0014).

Each `expect*()` returns a Handle. Before `await_all()`, the outcome is
PENDING and accessing `message` / `latency_ms` raises. After, the Handle is
frozen with the resolved outcome and survives scope teardown.
"""
from __future__ import annotations

import pickle

import pytest


def test_a_fresh_handle_should_report_pending_outcome() -> None:
    from core.scenario import Handle, Outcome

    h = Handle(topic="t", matcher_description="m", correlation_id="c")
    assert h.outcome is Outcome.PENDING


def test_was_fulfilled_should_be_false_before_resolution() -> None:
    from core.scenario import Handle

    h = Handle(topic="t", matcher_description="m", correlation_id="c")
    assert h.was_fulfilled() is False


def test_reading_message_before_resolution_should_raise() -> None:
    from core.scenario import Handle

    h = Handle(topic="t", matcher_description="m", correlation_id="c")
    with pytest.raises(RuntimeError):
        _ = h.message


def test_reading_latency_before_resolution_should_raise() -> None:
    from core.scenario import Handle

    h = Handle(topic="t", matcher_description="m", correlation_id="c")
    with pytest.raises(RuntimeError):
        _ = h.latency_ms


def test_the_handle_repr_should_not_contain_the_payload() -> None:
    from core.scenario import Handle, Outcome

    h = Handle(topic="orders.booked", matcher_description="m", correlation_id="c")
    h._message = {"isin": "US91282CJW46", "qty": 1_000_000}
    h.outcome = Outcome.PASS
    text = repr(h)
    assert "US91282CJW46" not in text
    assert "1_000_000" not in text
    assert "1000000" not in text


def test_the_handle_should_not_be_pickleable() -> None:
    from core.scenario import Handle

    h = Handle(topic="t", matcher_description="m", correlation_id="c")
    with pytest.raises((TypeError, pickle.PicklingError)):
        pickle.dumps(h)


def test_was_fulfilled_should_be_true_when_outcome_is_pass() -> None:
    from core.scenario import Handle, Outcome

    h = Handle(topic="t", matcher_description="m", correlation_id="c")
    h.outcome = Outcome.PASS
    assert h.was_fulfilled() is True


def test_was_fulfilled_should_be_false_when_outcome_is_timeout() -> None:
    from core.scenario import Handle, Outcome

    h = Handle(topic="t", matcher_description="m", correlation_id="c")
    h.outcome = Outcome.TIMEOUT
    assert h.was_fulfilled() is False


def test_was_fulfilled_should_be_false_when_outcome_is_fail() -> None:
    from core.scenario import Handle, Outcome

    h = Handle(topic="t", matcher_description="m", correlation_id="c")
    h.outcome = Outcome.FAIL
    assert h.was_fulfilled() is False


# ---------------------------------------------------------------------------
# Reporter-oriented data accessors (PRD-007 §3)
# ---------------------------------------------------------------------------


def test_a_fresh_handle_should_report_no_last_rejection_payload() -> None:
    from core.scenario import Handle

    h = Handle(topic="t", matcher_description="m", correlation_id="c")
    assert h.last_rejection_payload is None


def test_a_fresh_handle_should_report_no_matcher_expected_shape() -> None:
    from core.scenario import Handle

    h = Handle(topic="t", matcher_description="m", correlation_id="c")
    assert h.matcher_expected is None

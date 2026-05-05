"""Group F: Stage DSL error semantics. Negative-behaviour integration tests.

Covers test-plan items F1-F7 from
`docs/test-plans/0027-stage-integration-tests.md` — the per-call DSL
contract that the user-facing `expect`, `publish`, and `on` methods on a
Stage scenario reject `on=None` (`MissingTransportError`) and `on="typo"`
(`UnknownTransportError`) at every entry point.

The test plan's R3 fix is the focus here: by giving `on=` a `None`
default and raising the typed `MissingTransportError` explicitly, we
surface a framework concept rather than the generic Python `TypeError`
the user would get from a missing required keyword. F7 closes the
race-prone post-hoc `Handle.transport` mutation flagged by R2.
"""

from __future__ import annotations

from typing import Any

import pytest
from choreo.matchers import field_equals
from choreo.scenario import Outcome
from choreo.stage import MissingTransportError, UnknownTransportError

# A placeholder matcher used by every DSL-error test. The DSL-error
# tests do not exercise matching; any value the production code accepts
# works. Module-level constant rather than a factory function because
# the value is invariant across tests.
_PLACEHOLDER_MATCHER = field_equals("status", "ok")


# ---------------------------------------------------------------------------
# F1 — expect() with on omitted
# ---------------------------------------------------------------------------


async def test_stage_expect_should_raise_missing_transport_error_when_on_is_omitted(
    open_scope: Any,
) -> None:
    """F1. The DSL-method signature has `on: str | None = None`, so
    omitting `on=` does NOT trip Python's TypeError for a missing
    required keyword. Instead the framework raises
    `MissingTransportError` so the diagnostic names the framework
    concept rather than the Python interpreter mechanism.

    Covers R3 (the dead-letter MissingTransportError is now reachable).
    """
    with pytest.raises(MissingTransportError, match="on=None"):
        open_scope.expect("topic", _PLACEHOLDER_MATCHER)


# ---------------------------------------------------------------------------
# F2 — publish() with on omitted
# ---------------------------------------------------------------------------


async def test_stage_publish_should_raise_missing_transport_error_when_on_is_omitted(
    open_scope: Any,
) -> None:
    """F2. Same contract as F1 for the `publish` method."""
    with pytest.raises(MissingTransportError, match="on=None"):
        open_scope.publish("topic", {"hello": "world"})


# ---------------------------------------------------------------------------
# F3 — on() with on omitted
# ---------------------------------------------------------------------------


async def test_stage_on_should_raise_missing_transport_error_when_on_is_omitted(
    open_scope: Any,
) -> None:
    """F3. Same contract for the `on()` method (the entry point of a
    reactive reply chain). The trigger transport is what `on=` selects;
    omitting it gives the same `MissingTransportError` the other DSL
    methods do.
    """
    with pytest.raises(MissingTransportError, match="on=None"):
        open_scope.on("trigger.topic")


# ---------------------------------------------------------------------------
# F4 — expect() with unknown transport
# ---------------------------------------------------------------------------


async def test_stage_expect_should_raise_unknown_transport_error_when_on_names_an_unknown_transport(
    open_scope: Any,
) -> None:
    """F4. A typo in `on=` (e.g. `"ntas"` instead of `"nats"`) surfaces
    as `UnknownTransportError`, NOT as a generic `KeyError`. The error
    message lists the registered transport names so the user can spot
    the typo.
    """
    with pytest.raises(UnknownTransportError) as excinfo:
        open_scope.expect("topic", _PLACEHOLDER_MATCHER, on="ntas")

    msg = str(excinfo.value)
    assert "ntas" in msg
    # Both registered transports appear in the diagnostic, sorted for
    # deterministic assertions.
    assert "kafka" in msg
    assert "nats" in msg


# ---------------------------------------------------------------------------
# F5 — publish() with unknown transport
# ---------------------------------------------------------------------------


async def test_stage_publish_should_raise_unknown_transport_error_when_on_names_an_unknown_transport(
    open_scope: Any,
) -> None:
    """F5. Same contract as F4 for the `publish` method."""
    with pytest.raises(UnknownTransportError) as excinfo:
        open_scope.publish("topic", {"x": 1}, on="kfka")

    assert "kfka" in str(excinfo.value)


# ---------------------------------------------------------------------------
# F6 — StageReplyChain.publish() with unknown response transport
# ---------------------------------------------------------------------------


async def test_stage_reply_publish_should_raise_unknown_transport_error_when_response_on_is_a_typo(
    open_scope: Any,
) -> None:
    """F6. A reactive-reply chain's `.publish()` takes the RESPONSE
    transport's `on=`, which can differ from the trigger transport's
    `on=` (the cross-transport bridge case). A typo on the response
    side raises `UnknownTransportError` naming the typo'd value.

    The trigger leg uses a valid transport so the test exercises only
    the response-side guard.
    """
    chain = open_scope.on("trigger.topic", on="kafka")
    with pytest.raises(UnknownTransportError) as excinfo:
        chain.publish("response.topic", on="natz", build=lambda msg: msg)

    assert "natz" in str(excinfo.value)


# ---------------------------------------------------------------------------
# F7 — Handle.transport set at construction, not via post-hoc mutation
# ---------------------------------------------------------------------------


async def test_stage_expect_should_not_set_handle_transport_via_post_hoc_mutation(
    open_scope: Any,
) -> None:
    """F7. The framework must NOT set `Handle.transport` after the
    `Handle(...)` constructor call. The race the contract defends
    against is: `_register_expectation` returns a handle (transport
    unset), the dispatcher fires immediately on an inbound message,
    the matcher resolves the handle, THEN expect() sets transport ---
    too late.

    This integration-level test cannot inject a synthetic dispatcher
    fire between `Handle(...)` and the eventual return of `expect()`,
    so it asserts on the observable consequence:

      * `handle.transport == "kafka"` immediately after expect() returns,
        AND
      * `handle.outcome is Outcome.PENDING` (proving the wiring is
        deferred to Group G; nothing has resolved the handle yet).

    The structural defence — the absence of any `handle.transport = on`
    statement after `Handle(...)` construction — is enforced by code
    review of `_StageScenarioScope.expect` and by `Handle.transport`
    being a read-only `@property` (no public setter).

    Covers R2 (the race-prone post-hoc mutation pattern).
    """
    handle = open_scope.expect("topic", _PLACEHOLDER_MATCHER, on="kafka")

    # Observable consequence #1: transport is set by the time the user
    # holds a reference.
    assert handle.transport == "kafka"

    # Observable consequence #2: handle has not been resolved by any
    # dispatcher (which would be impossible during a race-mutation).
    assert handle.outcome is Outcome.PENDING

    # Structural defence: `Handle.transport` has no public setter.
    # Mutation attempts raise AttributeError because the dataclass
    # field is `_transport` and `transport` is a read-only property.
    with pytest.raises(AttributeError):
        handle.transport = "other"  # type: ignore[misc]

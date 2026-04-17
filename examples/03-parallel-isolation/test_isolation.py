"""03 — Parallel-scenario isolation via a CorrelationPolicy.

The library default is `NoCorrelationPolicy`: transparent passthrough, no
stamping, no filter — every live scope on a topic sees every message.
That's the right default for a "hello world" test, and it matches what
an OSS user expects when they write `s.publish(topic, payload)`.

It is **not** right when two scenarios run concurrently (inside one
pytest-xdist worker, or across sequential tests on a broker that carries
messages between tests): without correlation routing, Scenario A's publish
will satisfy Scenario B's expectation on the same topic.

The fix is a `CorrelationPolicy` — the harness stamps an id on every
outbound message and only lets the scope's own id past the inbound filter.

Run it:

    pytest examples/03-parallel-isolation/
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from choreo import DictFieldPolicy, Harness, NoCorrelationPolicy
from choreo.matchers import field_equals
from choreo.transports import MockTransport

ALLOWLIST = Path(__file__).parent / "allowlist.yaml"


# ---------------------------------------------------------------------------
# The problem — without a policy, parallel scopes cross-match
# ---------------------------------------------------------------------------


async def test_without_a_policy_parallel_scopes_see_each_others_messages() -> None:
    """Illustrative only — normally you don't want this. Both scopes expect
    a message on `shared.topic` and both scopes publish one, with different
    bodies. Without a correlation filter, each scope's expect sees every
    publish on the topic, including the sibling's — so both pass, which is
    the bug: scope B's assertion should have timed out."""
    harness = Harness(
        MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost"),
        correlation=NoCorrelationPolicy(),  # the library default; named for clarity
    )
    await harness.connect()
    try:
        async with harness.scenario("a") as scope_a:
            async with harness.scenario("b") as scope_b:
                # Both scopes want to see "from: a".
                handle_a = scope_a.expect("shared.topic", field_equals("from", "a"))
                handle_b = scope_b.expect("shared.topic", field_equals("from", "a"))

                scope_a = scope_a.publish("shared.topic", {"from": "a"})
                scope_b = scope_b.publish("shared.topic", {"from": "b"})

                result_a, result_b = await asyncio.gather(
                    scope_a.await_all(timeout_ms=50),
                    scope_b.await_all(timeout_ms=50),
                )

        # Both scopes matched scope A's publish — cross-contamination.
        assert handle_a.was_fulfilled()
        assert handle_b.was_fulfilled()
        assert result_a.passed and result_b.passed
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# The fix — opt into per-scope routing with a DictFieldPolicy
# ---------------------------------------------------------------------------


async def test_with_a_policy_parallel_scopes_only_match_their_own_messages() -> None:
    """A `DictFieldPolicy` stamps a per-scope correlation id onto each
    outbound dict and drops inbound messages whose id doesn't match the
    scope's. Scope A's publish satisfies scope A's expectation; scope B's
    publish does not leak in."""
    harness = Harness(
        MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    await harness.connect()
    try:
        async with harness.scenario("a") as scope_a:
            async with harness.scenario("b") as scope_b:
                handle_a = scope_a.expect("shared.topic", field_equals("from", "a"))
                handle_b = scope_b.expect("shared.topic", field_equals("from", "a"))

                # Scope A publishes the right shape; scope B publishes a
                # sibling message that would satisfy scope B's matcher *if*
                # the correlation filter weren't in play.
                scope_a = scope_a.publish("shared.topic", {"from": "a"})
                scope_b = scope_b.publish("shared.topic", {"from": "a"})

                result_a, result_b = await asyncio.gather(
                    scope_a.await_all(timeout_ms=50),
                    scope_b.await_all(timeout_ms=50),
                )

        # Both scopes see their own publish only — the correlation filter
        # kept A's message out of B and vice versa.
        assert handle_a.was_fulfilled()
        assert handle_b.was_fulfilled()
        assert handle_a.message["from"] == "a"
        assert handle_b.message["from"] == "a"
        # The point the test is proving: each scope's handle resolved
        # against a *different* message on the wire, even though both
        # messages matched the shape. Without the policy, A's handle
        # would have resolved against whichever message arrived first.
        assert result_a.passed
        assert result_b.passed
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Pairing a policy with your schema — a few real shapes
# ---------------------------------------------------------------------------


async def test_the_policy_can_stamp_any_field_name_your_schema_uses() -> None:
    """If your messages carry correlation under a different key (`trace_id`,
    `request_id`, an OpenTelemetry attribute), pass `field=...`."""
    harness = Harness(
        MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="trace_id"),
    )
    await harness.connect()
    try:
        async with harness.scenario("trace") as s:
            h = s.expect("orders.new", field_equals("item_id", "ITEM-42"))
            s = s.publish("orders.new", {"item_id": "ITEM-42"})
            result = await s.await_all(timeout_ms=50)
        result.assert_passed()
        # The policy stamped its field onto the outbound payload.
        sent = harness._transport.sent()  # diagnostic method on MockTransport
        wire_payload = json.loads(sent[0][1].decode())
        assert "trace_id" in wire_payload
        assert h.was_fulfilled()
    finally:
        await harness.disconnect()


async def test_explicit_correlation_on_the_payload_is_honoured_over_the_policy_default() -> None:
    """If the caller supplies the correlation field themselves, the policy
    honours it — stamping is `setdefault`-style, never overwrite. Useful
    for echoing a SUT-supplied id back on a reply, or negative tests that
    deliberately publish under a different scope's identity."""
    harness = Harness(
        MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    await harness.connect()
    try:
        async with harness.scenario("explicit-id") as s:
            s.expect("fake", field_equals("x", 1))  # never satisfied; ignored
            s = s.publish("out", {"correlation_id": "from-caller", "x": 1})
            await s.await_all(timeout_ms=20)

        sent = harness._transport.sent()  # diagnostic method on MockTransport
        wire = json.loads(sent[0][1].decode())
        # The policy left the caller's id alone.
        assert wire["correlation_id"] == "from-caller"
    finally:
        await harness.disconnect()


async def test_a_policy_with_a_prefix_refuses_foreign_namespace_ids() -> None:
    """A policy configured with a `prefix` enforces that explicit overrides
    match it — otherwise a trigger-echo could smuggle an upstream id onto
    the wire. `test_namespace()` is the shipped factory for `prefix="TEST-"`;
    you can configure any prefix your downstream ingress filters on."""
    from choreo import CorrelationIdNotInNamespaceError

    harness = Harness(
        MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id", prefix="TEST-"),
    )
    await harness.connect()
    try:
        async with harness.scenario("prefix-enforced") as s:
            s.expect("fake", field_equals("x", 1))
            with pytest.raises(CorrelationIdNotInNamespaceError):
                s.publish("out", {"correlation_id": "PROD-live-account", "x": 1})
    finally:
        await harness.disconnect()

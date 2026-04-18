"""Behavioural tests for the CorrelationPolicy surface (ADR-0019).

Covers the three shipped profiles (NoCorrelationPolicy, DictFieldPolicy,
test_namespace) against the Envelope shape, the Harness wiring, and the
Scenario inbound/outbound flow.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from choreo import (
    CorrelationIdNotInNamespaceError,
    CorrelationPolicy,
    CorrelationPolicyError,
    DictFieldPolicy,
    Envelope,
    Harness,
    NoCorrelationPolicy,
)
from choreo import test_namespace as _ns
from choreo.transports import MockTransport


def _mock_transport(allowlist_yaml_path: Path) -> MockTransport:
    return MockTransport(
        allowlist_path=allowlist_yaml_path,
        endpoint="mock://localhost",
    )


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def test_an_envelope_should_be_frozen() -> None:
    """Envelope is a value object passed around the policy surface. It must
    be immutable so a buggy policy cannot mutate it and surprise later
    callers that hold the same reference."""
    env = Envelope(topic="t", payload={"k": "v"})
    with pytest.raises(AttributeError):
        env.topic = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NoCorrelationPolicy
# ---------------------------------------------------------------------------


async def test_no_correlation_policy_should_generate_none_as_the_scope_id() -> None:
    """Under the no-op policy, the scope has no correlation id and
    Scenario.correlation_id is None (ADR-0019)."""
    policy = NoCorrelationPolicy()
    assert await policy.new_id() is None


def test_no_correlation_policy_should_not_stamp_outbound_payloads() -> None:
    """write() is an identity function — the payload arrives on the wire
    exactly as the caller supplied it."""
    policy = NoCorrelationPolicy()
    env = Envelope(topic="orders.new", payload={"order_id": 42})
    result = policy.write(env, "any-id")
    assert result is env
    assert result.payload == {"order_id": 42}


def test_no_correlation_policy_should_return_none_on_read() -> None:
    """read() returning None is the broadcast-fallback signal in Scenario."""
    policy = NoCorrelationPolicy()
    assert policy.read(Envelope(topic="t", payload={"correlation_id": "TEST-abc"})) is None


def test_no_correlation_policy_should_declare_no_routing() -> None:
    """Negative-assertion matchers will gate on this flag (ADR-0019)."""
    assert NoCorrelationPolicy().routes_by_correlation is False


# ---------------------------------------------------------------------------
# DictFieldPolicy
# ---------------------------------------------------------------------------


async def test_dict_field_policy_should_stamp_a_missing_field() -> None:
    policy = DictFieldPolicy(field="trace_id")
    env = Envelope(topic="t", payload={"k": "v"})
    result = policy.write(env, "abc123")
    assert result.payload == {"k": "v", "trace_id": "abc123"}


async def test_dict_field_policy_should_not_mutate_the_input_envelope() -> None:
    """ADR-0019 §Implementation: `write` returns a new envelope; it must
    not mutate the caller's payload in place."""
    policy = DictFieldPolicy()
    original_payload = {"k": "v"}
    env = Envelope(topic="t", payload=original_payload)
    policy.write(env, "x")
    assert original_payload == {"k": "v"}


async def test_dict_field_policy_should_honour_an_explicit_field() -> None:
    """An explicit field value on the payload is not overwritten — mirrors
    the pre-ADR-0019 `setdefault` semantic."""
    policy = DictFieldPolicy()
    env = Envelope(topic="t", payload={"correlation_id": "mine", "k": "v"})
    result = policy.write(env, "from-scope")
    assert result.payload["correlation_id"] == "mine"


async def test_dict_field_policy_should_not_stamp_non_dict_payloads() -> None:
    policy = DictFieldPolicy()
    env = Envelope(topic="t", payload=b"raw bytes")
    result = policy.write(env, "any-id")
    assert result.payload == b"raw bytes"


async def test_dict_field_policy_should_read_the_configured_field() -> None:
    policy = DictFieldPolicy(field="trace_id")
    assert policy.read(Envelope(topic="t", payload={"trace_id": "abc"})) == "abc"


async def test_dict_field_policy_should_return_none_when_the_field_is_missing() -> None:
    policy = DictFieldPolicy(field="trace_id")
    assert policy.read(Envelope(topic="t", payload={"k": "v"})) is None


async def test_dict_field_policy_should_declare_routing() -> None:
    assert DictFieldPolicy().routes_by_correlation is True


# ---------------------------------------------------------------------------
# DictFieldPolicy with a prefix — namespace enforcement
# ---------------------------------------------------------------------------


async def test_dict_field_policy_with_prefix_should_refuse_a_non_prefixed_override() -> None:
    """An explicit scope correlation id that does not match the policy's
    prefix raises — otherwise a foreign id could leak onto the wire."""
    policy = DictFieldPolicy(prefix="TEST-")
    env = Envelope(topic="t", payload={"k": "v"})
    with pytest.raises(CorrelationIdNotInNamespaceError):
        policy.write(env, "PROD-abc")


async def test_dict_field_policy_with_prefix_should_refuse_a_non_prefixed_explicit_field() -> None:
    """A caller supplying an explicit correlation_id on the payload must
    also meet the prefix rule — otherwise a trigger-echo could smuggle
    an upstream id back onto the wire."""
    policy = DictFieldPolicy(prefix="TEST-")
    env = Envelope(topic="t", payload={"correlation_id": "PROD-abc", "k": "v"})
    with pytest.raises(CorrelationIdNotInNamespaceError):
        policy.write(env, "TEST-scope")


async def test_dict_field_policy_with_prefix_should_accept_a_matching_explicit_field() -> None:
    policy = DictFieldPolicy(prefix="TEST-")
    env = Envelope(topic="t", payload={"correlation_id": "TEST-mine", "k": "v"})
    result = policy.write(env, "TEST-scope")
    assert result.payload["correlation_id"] == "TEST-mine"


# ---------------------------------------------------------------------------
# test_namespace() factory
# ---------------------------------------------------------------------------


async def test_the_test_namespace_factory_should_produce_a_test_prefixed_id() -> None:
    cid = await _ns().new_id()
    assert cid.startswith("TEST-")


async def test_the_test_namespace_factory_should_accept_a_custom_field_name() -> None:
    """Consumers who use a different field name still want the TEST- prefix
    for the ingress-filter contract."""
    policy = _ns(field="trace_id")
    env = Envelope(topic="t", payload={"k": "v"})
    result = policy.write(env, await policy.new_id())
    assert "trace_id" in result.payload
    assert result.payload["trace_id"].startswith("TEST-")


# ---------------------------------------------------------------------------
# Harness default + WARNING on non-Mock with NoCorrelationPolicy
# ---------------------------------------------------------------------------


async def test_the_default_harness_policy_should_be_no_correlation(
    allowlist_yaml_path: Path,
) -> None:
    """ADR-0019 §Decision: public release default is NoCorrelationPolicy.
    Consumers who want per-scope isolation opt in explicitly."""
    harness = Harness(_mock_transport(allowlist_yaml_path))
    assert isinstance(harness.correlation, NoCorrelationPolicy)
    assert await harness.correlation.new_id() is None


async def test_an_explicit_correlation_argument_should_override_the_default(
    allowlist_yaml_path: Path,
) -> None:
    policy = NoCorrelationPolicy()
    harness = Harness(_mock_transport(allowlist_yaml_path), correlation=policy)
    assert harness.correlation is policy


async def test_no_op_policy_against_mock_transport_should_not_warn(
    allowlist_yaml_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MockTransport is the safe pairing for NoCorrelationPolicy (no real
    broker, no cross-tenant fan-out). The WARNING must not fire."""
    harness = Harness(
        _mock_transport(allowlist_yaml_path),
        correlation=NoCorrelationPolicy(),
    )
    with caplog.at_level(logging.WARNING, logger="choreo.harness"):
        await harness.connect()
        try:
            pass
        finally:
            await harness.disconnect()
    warnings = [r for r in caplog.records if "correlator_noop" in r.getMessage()]
    assert warnings == []


async def test_no_op_policy_against_non_mock_transport_should_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ADR-0019 §Defence-in-depth regression: WARNING fires whenever
    NoCorrelationPolicy is paired with a non-Mock transport so an operator
    sees the unsafe pairing at connect() time."""

    class _FakeTransport:
        # Not a MockTransport — class name alone is checked at connect().
        async def connect(self) -> None: ...

        async def disconnect(self) -> None: ...

        def clear_subscriptions(self) -> None: ...

        def subscribe(self, topic: str, callback) -> None: ...

        def unsubscribe(self, topic: str, callback) -> None: ...

        def publish(self, topic: str, payload: bytes, *, on_sent=None) -> None: ...

        def active_subscription_count(self) -> int:
            return 0

    harness = Harness(_FakeTransport(), correlation=NoCorrelationPolicy())  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="choreo.harness"):
        await harness.connect()
    messages = [r.getMessage() for r in caplog.records]
    assert any("correlator_noop_against_real_transport" in m for m in messages)
    assert any("_FakeTransport" in m for m in messages)


# ---------------------------------------------------------------------------
# Scenario default flow — end-to-end through the policy
# ---------------------------------------------------------------------------


async def test_scenario_under_test_namespace_should_stamp_outbound_payloads(
    allowlist_yaml_path: Path,
) -> None:
    """End-to-end: with `test_namespace()` explicitly configured, a
    scenario publish stamps the configured dict field. This reproduces
    the pre-ADR-0019 captive behaviour."""
    from choreo.matchers import payload_contains

    harness = Harness(_mock_transport(allowlist_yaml_path), correlation=_ns())
    await harness.connect()
    try:
        async with harness.scenario("stamp") as s:
            s.expect("unused", payload_contains({"never": "seen"}))
            s = s.publish("out", {"k": "v"})
            await s.await_all(timeout_ms=10)
    finally:
        await harness.disconnect()
    published = harness._transport.sent()  # type: ignore[attr-defined]
    wire_entries = [p for p in published if p[0] == "out"]
    assert len(wire_entries) == 1
    import json

    wire = json.loads(wire_entries[0][1].decode())
    assert wire["k"] == "v"
    assert wire["correlation_id"].startswith("TEST-")


async def test_scenario_under_no_correlation_policy_should_not_stamp(
    allowlist_yaml_path: Path,
) -> None:
    """ADR-0019 core promise: under NoCorrelationPolicy, `s.publish(topic,
    payload)` produces exactly `payload` on the wire. No library-injected
    field."""
    from choreo.matchers import payload_contains

    harness = Harness(
        _mock_transport(allowlist_yaml_path),
        correlation=NoCorrelationPolicy(),
    )
    await harness.connect()
    try:
        async with harness.scenario("transparent") as s:
            s.expect("unused", payload_contains({"never": "seen"}))
            s = s.publish("out", {"order_id": 42})
            await s.await_all(timeout_ms=10)
    finally:
        await harness.disconnect()
    published = harness._transport.sent()  # type: ignore[attr-defined]
    wire_entries = [p for p in published if p[0] == "out"]
    import json

    wire = json.loads(wire_entries[0][1].decode())
    assert wire == {"order_id": 42}


async def test_scenario_correlation_id_should_be_none_under_no_correlation_policy(
    allowlist_yaml_path: Path,
) -> None:
    """Scope.correlation_id surfaces the policy's generated id; NoCorrelationPolicy
    returns None from new_id() so callers doing manual stamping see None
    rather than a silent empty-string sentinel."""
    harness = Harness(
        _mock_transport(allowlist_yaml_path),
        correlation=NoCorrelationPolicy(),
    )
    await harness.connect()
    try:
        async with harness.scenario("none-id") as s:
            assert s.correlation_id is None
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Policy exception wrapping
# ---------------------------------------------------------------------------


class _BrokenWritePolicy:
    """Raises from write(); used to verify the library wraps policy errors."""

    async def new_id(self) -> str:
        return "id"

    def write(self, envelope: Envelope, correlation_id: str) -> Envelope:
        raise RuntimeError("write blew up")

    def read(self, envelope: Envelope) -> str | None:
        return None

    @property
    def routes_by_correlation(self) -> bool:
        return True


async def test_a_broken_policy_write_should_raise_correlation_policy_error(
    allowlist_yaml_path: Path,
) -> None:
    """ADR-0019 §Security Considerations §Consumer-supplied policy trust
    boundary: a policy exception must surface as CorrelationPolicyError
    naming the policy class, not a bare RuntimeError."""
    from choreo.matchers import payload_contains

    harness = Harness(
        _mock_transport(allowlist_yaml_path),
        correlation=_BrokenWritePolicy(),
    )
    await harness.connect()
    try:
        async with harness.scenario("broken") as s:
            s.expect("unused", payload_contains({"never": "seen"}))
            with pytest.raises(CorrelationPolicyError) as excinfo:
                s.publish("out", {"k": "v"})
        err = excinfo.value
        assert err.policy_class == "_BrokenWritePolicy"
        assert err.method == "write"
        assert isinstance(err.original, RuntimeError)
    finally:
        await harness.disconnect()


class _BrokenNewIdPolicy:
    async def new_id(self) -> str:
        raise RuntimeError("new_id blew up")

    def write(self, envelope: Envelope, correlation_id: str) -> Envelope:
        return envelope

    def read(self, envelope: Envelope) -> str | None:
        return None

    @property
    def routes_by_correlation(self) -> bool:
        return True


async def test_a_broken_new_id_should_fail_scope_entry(
    allowlist_yaml_path: Path,
) -> None:
    harness = Harness(
        _mock_transport(allowlist_yaml_path),
        correlation=_BrokenNewIdPolicy(),
    )
    await harness.connect()
    try:
        with pytest.raises(CorrelationPolicyError) as excinfo:
            async with harness.scenario("broken") as _s:
                pass
        err = excinfo.value
        assert err.policy_class == "_BrokenNewIdPolicy"
        assert err.method == "new_id"
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Custom payload-field policy satisfies the Protocol
# ---------------------------------------------------------------------------


class _HeaderStyleStubPolicy:
    """Stub used as a regression gate: a policy that doesn't touch the
    payload field still satisfies the CorrelationPolicy protocol. Without
    the envelope-shaped surface (ADR-0019), this kind of policy would
    require a protocol change.
    """

    def __init__(self) -> None:
        self._counter = 0
        self.headers_written: list[tuple[str, str]] = []

    async def new_id(self) -> str:
        self._counter += 1
        return f"h-{self._counter}"

    def write(self, envelope: Envelope, correlation_id: str) -> Envelope:
        # Would stamp into envelope.headers in a real header policy.
        self.headers_written.append((envelope.topic, correlation_id))
        return Envelope(
            topic=envelope.topic,
            payload=envelope.payload,
            headers={**envelope.headers, "x-correlation": correlation_id},
        )

    def read(self, envelope: Envelope) -> str | None:
        return envelope.headers.get("x-correlation")

    @property
    def routes_by_correlation(self) -> bool:
        return True


def test_a_header_style_policy_should_satisfy_the_correlation_protocol() -> None:
    """Regression gate against re-introducing payload-field-only assumptions
    into the CorrelationPolicy protocol. A header-style policy that does
    not touch the payload must still be a CorrelationPolicy."""
    policy: CorrelationPolicy = _HeaderStyleStubPolicy()
    assert isinstance(policy, CorrelationPolicy)

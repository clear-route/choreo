"""Behavioural tests for the allowlist guard (ADR-0006).

The library owns the `Allowlist` primitive; each **transport** enforces it
at `connect()`. These tests use `MockTransport` as the enforcement exemplar —
any real transport (NatsTransport, KafkaTransport, …) follows the same
pattern with its own field-to-category mapping.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Allowlist loading — generic category-keyed YAML
# ---------------------------------------------------------------------------


def test_load_allowlist_should_load_a_flat_category_mapping(
    allowlist_yaml_path: Path,
) -> None:
    from choreo.environment import load_allowlist

    allowlist = load_allowlist(allowlist_yaml_path)
    assert "mock://localhost" in allowlist.get("mock_endpoints")


def test_allowlist_get_should_return_empty_tuple_for_unknown_category(
    allowlist_yaml_path: Path,
) -> None:
    from choreo.environment import load_allowlist

    allowlist = load_allowlist(allowlist_yaml_path)
    assert allowlist.get("some_future_transport_category") == ()


def test_load_allowlist_should_reject_a_non_mapping_top_level(tmp_path: Path) -> None:
    from choreo.environment import AllowlistConfigError, load_allowlist

    bad = tmp_path / "bad.yaml"
    bad.write_text("[1, 2, 3]\n")
    with pytest.raises(AllowlistConfigError):
        load_allowlist(bad)


def test_load_allowlist_should_reject_a_category_whose_value_is_not_a_list(
    tmp_path: Path,
) -> None:
    from choreo.environment import AllowlistConfigError, load_allowlist

    bad = tmp_path / "bad.yaml"
    bad.write_text('mock_endpoints: "not-a-list"\n')
    with pytest.raises(AllowlistConfigError):
        load_allowlist(bad)


# ---------------------------------------------------------------------------
# MockTransport allowlist enforcement at connect()
# ---------------------------------------------------------------------------


async def test_transport_should_refuse_to_connect_with_an_endpoint_outside_the_allowlist(
    allowlist_yaml_path: Path,
) -> None:
    from choreo.environment import HostNotInAllowlist
    from choreo.transports import MockTransport

    transport = MockTransport(
        allowlist_path=allowlist_yaml_path,
        endpoint="prod.example.internal:15380",
    )
    with pytest.raises(HostNotInAllowlist) as exc:
        await transport.connect()

    assert "prod.example.internal:15380" in str(exc.value)
    assert str(allowlist_yaml_path) in str(exc.value)


async def test_transport_should_connect_when_every_field_is_on_the_allowlist(
    allowlist_yaml_path: Path,
) -> None:
    from choreo.transports import MockTransport

    transport = MockTransport(
        allowlist_path=allowlist_yaml_path,
        endpoint="mock://localhost",
    )
    await transport.connect()
    await transport.disconnect()


async def test_transport_without_an_allowlist_path_should_skip_enforcement(
    allowlist_yaml_path: Path,
) -> None:
    """A MockTransport constructed with no allowlist is pure in-memory
    plumbing with no guard. Useful for tests that only care about routing /
    dispatch behaviour."""
    from choreo.transports import MockTransport

    transport = MockTransport()  # no allowlist_path, no fields set
    await transport.connect()
    await transport.disconnect()


# ---------------------------------------------------------------------------
# Harness correlation prefix — applied regardless of transport
# ---------------------------------------------------------------------------


async def test_a_connected_harness_configured_with_test_namespace_should_stamp_the_prefix(
    allowlist_yaml_path: Path,
) -> None:
    """A harness configured with `test_namespace()` stamps a `TEST-` prefix
    onto outbound correlation ids — the opt-in posture for consumers who
    want the pre-ADR-0019 ingress-filter contract."""
    from choreo import Harness, test_namespace
    from choreo.transports import MockTransport

    transport = MockTransport(
        allowlist_path=allowlist_yaml_path,
        endpoint="mock://localhost",
    )
    harness = Harness(transport, correlation=test_namespace())
    await harness.connect()
    try:
        cid = await harness.correlation.new_id()
        assert cid is not None
        assert cid.startswith("TEST-")
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Correlation-ID generator — DictFieldPolicy / test_namespace factory
# ---------------------------------------------------------------------------


async def test_test_namespace_ids_should_start_with_the_test_prefix() -> None:
    from choreo import test_namespace

    cid = await test_namespace().new_id()
    assert cid.startswith("TEST-")


async def test_test_namespace_ids_should_be_unique() -> None:
    from choreo import test_namespace

    policy = test_namespace()
    ids = {await policy.new_id() for _ in range(200)}
    assert len(ids) == 200


async def test_test_namespace_ids_should_be_unguessable() -> None:
    from choreo import test_namespace

    cid = await test_namespace().new_id()
    suffix = cid.removeprefix("TEST-")
    assert len(suffix) >= 32

"""Unit tests for KafkaTransport — paths exercisable without a broker.

The full round-trip is covered by the e2e contract suite (``pytest -m e2e``)
against a real Kafka broker. These tests only cover behaviours that don't
require a live cluster:

  - constructor validation
  - allowlist enforcement at connect time
  - a clear TransportError when the optional aiokafka dependency is absent
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from choreo.environment import HostNotInAllowlist
from choreo.transports import KafkaTransport, TransportError


def test_a_kafka_transport_constructed_with_no_bootstrap_servers_should_raise() -> None:
    with pytest.raises(ValueError):
        KafkaTransport(bootstrap_servers=[])


async def test_a_kafka_transport_should_refuse_to_connect_to_a_broker_outside_the_allowlist(
    allowlist_yaml_path: Path,
) -> None:
    transport = KafkaTransport(
        bootstrap_servers=["prod.internal:9092"],
        allowlist_path=allowlist_yaml_path,
    )
    with pytest.raises(HostNotInAllowlist) as exc:
        await transport.connect()
    assert "prod.internal" in str(exc.value)


async def test_a_kafka_transport_should_raise_transport_error_when_aiokafka_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Simulate the extra being absent. If aiokafka isn't installed at all
    we don't need this; if it is, hiding it forces the ImportError path."""
    # Force the ImportError branch regardless of whether aiokafka is installed.
    monkeypatch.setitem(sys.modules, "aiokafka", None)
    transport = KafkaTransport(bootstrap_servers=["localhost:9092"])
    with pytest.raises(TransportError) as exc:
        await transport.connect()
    assert "aiokafka" in str(exc.value)
    assert "choreo[kafka]" in str(exc.value)

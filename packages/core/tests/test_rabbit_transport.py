"""Unit tests for RabbitTransport — paths exercisable without a broker.

Round-trip behaviour is covered by the e2e contract suite against a real
RabbitMQ broker. These tests only cover:

  - constructor validation
  - allowlist enforcement at connect time
  - a clear TransportError when the optional aio-pika dependency is absent
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from choreo.environment import HostNotInAllowlist
from choreo.transports import RabbitTransport, TransportError


def test_a_rabbit_transport_constructed_with_no_url_should_raise() -> None:
    with pytest.raises(ValueError):
        RabbitTransport(url="")


async def test_a_rabbit_transport_should_refuse_to_connect_to_a_broker_outside_the_allowlist(
    allowlist_yaml_path: Path,
) -> None:
    transport = RabbitTransport(
        url="amqp://guest:guest@prod.internal:5672/",
        allowlist_path=allowlist_yaml_path,
    )
    with pytest.raises(HostNotInAllowlist) as exc:
        await transport.connect()
    assert "prod.internal" in str(exc.value)


async def test_a_rabbit_transport_should_raise_transport_error_when_aio_pika_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "aio_pika", None)
    transport = RabbitTransport(url="amqp://guest:guest@localhost:5672/")
    with pytest.raises(TransportError) as exc:
        await transport.connect()
    assert "aio-pika" in str(exc.value)
    assert "choreo[rabbitmq]" in str(exc.value)


async def test_a_rabbit_transport_should_not_leak_credentials_in_connect_errors(
    tmp_path: Path,
) -> None:
    """TransportError must not echo the user/password segment of the URL.
    Those strings reach CI logs and captured test output; they must stay in
    memory only."""
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text('amqp_brokers:\n  - "amqp://sensitive_user:secret_pw@127.0.0.1:1/"\n')
    transport = RabbitTransport(
        url="amqp://sensitive_user:secret_pw@127.0.0.1:1/",
        allowlist_path=allowlist,
        connect_timeout_s=0.05,
    )
    with pytest.raises(TransportError) as exc:
        await transport.connect()
    message = str(exc.value)
    assert "sensitive_user" not in message
    assert "secret_pw" not in message
    assert "<redacted>" in message

"""Unit tests for LbmTransport — paths exercisable without a broker.

Round-trip behaviour is covered by the e2e contract suite against a real
LBM messaging system. These tests only cover:

  - constructor validation (config file, license file)
  - a clear LbmError when the optional pylbm dependency is absent
  - lifecycle methods (connect/disconnect)
  - subscription tracking
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from choreo.transports.lbm import LbmError, LbmTransport


def test_an_lbm_transport_constructed_with_no_config_file_should_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When neither lbm_config_file parameter nor LBM_CONFIG_FILE env var is
    set, the constructor should raise immediately."""
    monkeypatch.delenv("LBM_CONFIG_FILE", raising=False)
    with pytest.raises(ValueError) as exc:
        LbmTransport()
    assert "lbm_config_file must be provided" in str(exc.value)


def test_an_lbm_transport_constructed_with_nonexistent_config_file_should_raise(
    tmp_path: Path,
) -> None:
    """When the config file path is provided but the file does not exist,
    the constructor should raise."""
    nonexistent = tmp_path / "nonexistent.xml"
    with pytest.raises(ValueError) as exc:
        LbmTransport(lbm_config_file=nonexistent, license_file="/dev/null")
    assert "config file not found" in str(exc.value)


def test_an_lbm_transport_should_read_config_path_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When LBM_CONFIG_FILE env var is set and the file exists, the
    constructor should accept it."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")
    monkeypatch.setenv("LBM_CONFIG_FILE", str(config))
    monkeypatch.setenv("LBM_LICENSE_FILENAME", "/dev/null")
    transport = LbmTransport()
    assert transport._config_file == config


def test_an_lbm_transport_constructed_with_no_license_should_raise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When neither license_file parameter nor LBM_LICENSE_FILENAME env var
    is set, the constructor should raise."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")
    monkeypatch.delenv("LBM_LICENSE_FILENAME", raising=False)
    with pytest.raises(ValueError) as exc:
        LbmTransport(lbm_config_file=config)
    assert "license file must be provided" in str(exc.value)


def test_an_lbm_transport_should_set_license_env_var_when_provided(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When license_file parameter is provided, the constructor should set
    the LBM_LICENSE_FILENAME environment variable."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")
    license_path = tmp_path / "license.txt"
    license_path.write_text("LICENSE_KEY=ABC123")

    monkeypatch.delenv("LBM_LICENSE_FILENAME", raising=False)
    LbmTransport(lbm_config_file=config, license_file=license_path)

    import os

    assert os.environ["LBM_LICENSE_FILENAME"] == str(license_path)


async def test_an_lbm_transport_should_raise_lbm_error_when_pylbm_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When pylbm module is not installed, connect() should raise LbmError
    with a helpful message."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")

    monkeypatch.setitem(sys.modules, "pylbm", None)
    transport = LbmTransport(lbm_config_file=config, license_file="/dev/null")

    with pytest.raises(LbmError) as exc:
        await transport.connect()
    assert "pylbm" in str(exc.value)
    assert "LBM_LICENSE_FILENAME" in str(exc.value)


def test_an_lbm_transport_should_not_be_picklable(
    tmp_path: Path,
) -> None:
    """LbmTransport contains thread pools and LBM context objects that cannot
    be pickled. The __reduce__ override should raise TypeError."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")
    transport = LbmTransport(lbm_config_file=config, license_file="/dev/null")

    import pickle

    with pytest.raises(TypeError) as exc:
        pickle.dumps(transport)
    assert "does not support pickling" in str(exc.value)


async def test_subscribe_should_raise_when_not_connected(
    tmp_path: Path,
) -> None:
    """Attempting to subscribe before connect() should raise RuntimeError."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")
    transport = LbmTransport(lbm_config_file=config, license_file="/dev/null")

    def callback(topic: str, payload: bytes) -> None:
        pass

    with pytest.raises(RuntimeError) as exc:
        transport.subscribe("test.topic", callback)
    assert "not connected" in str(exc.value)


async def test_publish_should_raise_when_not_connected(
    tmp_path: Path,
) -> None:
    """Attempting to publish before connect() should raise RuntimeError."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")
    transport = LbmTransport(lbm_config_file=config, license_file="/dev/null")

    with pytest.raises(RuntimeError) as exc:
        transport.publish("test.topic", b"payload")
    assert "not connected" in str(exc.value)


async def test_disconnect_should_be_idempotent(
    tmp_path: Path,
) -> None:
    """Calling disconnect() multiple times should not raise."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")
    transport = LbmTransport(lbm_config_file=config, license_file="/dev/null")

    # Should not raise even though transport is not connected
    await transport.disconnect()
    await transport.disconnect()


async def test_active_subscription_count_should_track_subscriptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """active_subscription_count() should return the total number of active
    subscriptions across all topics."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")

    # Mock the pylbm module to avoid needing a real LBM install
    mock_lbm = MagicMock()
    mock_lbm.LbmContext.return_value = MagicMock()
    mock_lbm.LbmEventQueue.return_value = MagicMock()
    mock_lbm.LbmReceiverTopicAttributes.return_value = MagicMock()
    mock_lbm.LbmReceiver.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "pylbm", mock_lbm)

    transport = LbmTransport(lbm_config_file=config, license_file="/dev/null")

    # Before connect, count should be 0
    assert transport.active_subscription_count() == 0

    # Mock lbm_read_xml_config as a no-op function
    mock_lbm.lbm_read_xml_config = Mock()

    await transport.connect()

    # Subscribe to two different topics
    def callback1(topic: str, payload: bytes) -> None:
        pass

    def callback2(topic: str, payload: bytes) -> None:
        pass

    transport.subscribe("topic.one", callback1)
    assert transport.active_subscription_count() == 1

    transport.subscribe("topic.two", callback2)
    assert transport.active_subscription_count() == 2

    # Subscribing same callback to same topic again should increment
    transport.subscribe("topic.one", callback1)
    assert transport.active_subscription_count() == 3

    await transport.disconnect()


async def test_unsubscribe_should_remove_specific_subscription(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """unsubscribe() should remove only the specified callback for the given
    topic, leaving other subscriptions intact."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")

    # Mock the pylbm module
    mock_lbm = MagicMock()
    mock_ctx = MagicMock()
    mock_lbm.LbmContext.return_value = mock_ctx
    mock_lbm.LbmEventQueue.return_value = MagicMock()
    mock_lbm.LbmReceiverTopicAttributes.return_value = MagicMock()

    mock_receiver1 = MagicMock()
    mock_receiver2 = MagicMock()
    mock_lbm.LbmReceiver.side_effect = [mock_receiver1, mock_receiver2]

    monkeypatch.setitem(sys.modules, "pylbm", mock_lbm)
    mock_lbm.lbm_read_xml_config = Mock()

    transport = LbmTransport(lbm_config_file=config, license_file="/dev/null")
    await transport.connect()

    def callback1(topic: str, payload: bytes) -> None:
        pass

    def callback2(topic: str, payload: bytes) -> None:
        pass

    transport.subscribe("topic.one", callback1)
    transport.subscribe("topic.one", callback2)
    assert transport.active_subscription_count() == 2

    # Unsubscribe callback1
    transport.unsubscribe("topic.one", callback1)
    assert transport.active_subscription_count() == 1

    # Verify receiver1 was destroyed
    mock_receiver1.destroy.assert_called_once()
    mock_receiver2.destroy.assert_not_called()

    await transport.disconnect()


async def test_clear_subscriptions_should_clear_tracking_dict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clear_subscriptions() should clear the internal subscription tracking
    dictionary (used by test harness on scope teardown)."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")

    # Mock the pylbm module
    mock_lbm = MagicMock()
    mock_lbm.LbmContext.return_value = MagicMock()
    mock_lbm.LbmEventQueue.return_value = MagicMock()
    mock_lbm.LbmReceiverTopicAttributes.return_value = MagicMock()
    mock_lbm.LbmReceiver.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "pylbm", mock_lbm)
    mock_lbm.lbm_read_xml_config = Mock()

    transport = LbmTransport(lbm_config_file=config, license_file="/dev/null")
    await transport.connect()

    def callback(topic: str, payload: bytes) -> None:
        pass

    transport.subscribe("topic.one", callback)
    assert transport.active_subscription_count() == 1

    transport.clear_subscriptions()
    assert transport.active_subscription_count() == 0

    await transport.disconnect()


async def test_transport_capabilities_should_match_lbm_semantics(
    tmp_path: Path,
) -> None:
    """LbmTransport.capabilities should declare broadcast_fanout=True,
    loses_messages_without_subscriber=True, and ordered_per_topic=True,
    matching LBM's fire-and-forget multicast semantics."""
    config = tmp_path / "lbm_config.xml"
    config.write_text("<lbm-config></lbm-config>")
    transport = LbmTransport(lbm_config_file=config, license_file="/dev/null")

    caps = transport.capabilities
    assert caps.broadcast_fanout is True
    assert caps.loses_messages_without_subscriber is True
    assert caps.ordered_per_topic is True

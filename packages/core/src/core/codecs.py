"""Payload codecs — symmetric (decode + encode) bytes-to-Python translators.

A codec turns wire bytes into something matchers can work on (`decode`), and
turns a Python value into wire bytes the transport can ship (`encode`). The
Harness holds one (default JSON). Transports deal in bytes only.

Consumers can plug in their own codec for Avro, protobuf, tag-value, etc.
The default JSONCodec falls back to raw bytes if the payload doesn't parse as
JSON, so tests with mixed or opaque payloads still work on the receive side.
"""
from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Codec(Protocol):
    """Contract every codec implements. Pure functions, no state needed."""

    def decode(self, raw: bytes) -> Any: ...

    def encode(self, obj: Any) -> bytes: ...


class JSONCodec:
    """Attempts JSON decode; returns raw bytes if the payload is not JSON.

    `encode` requires a JSON-serializable Python value (dict, list, str, int,
    float, bool, None). Passing `bytes` is a programming error — bytes go
    straight to `Scenario.publish` / `Harness.publish` without touching the
    codec, so reaching this method with bytes means a dispatch bug upstream.
    """

    def decode(self, raw: bytes) -> Any:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            return raw

    def encode(self, obj: Any) -> bytes:
        if isinstance(obj, (bytes, bytearray)):
            raise TypeError(
                "JSONCodec.encode received bytes; pass bytes directly to "
                "publish() instead of routing them through the codec"
            )
        return json.dumps(obj, separators=(",", ":")).encode("utf-8")


class RawCodec:
    """Passes bytes through unchanged. Useful for binary-only backends."""

    def decode(self, raw: bytes) -> bytes:
        return raw

    def encode(self, obj: Any) -> bytes:
        if not isinstance(obj, (bytes, bytearray)):
            raise TypeError(
                f"RawCodec.encode requires bytes, got {type(obj).__name__}; "
                "RawCodec is for binary-only backends — use JSONCodec or a "
                "custom codec to publish structured payloads"
            )
        return bytes(obj)

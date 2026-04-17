"""Behavioural tests for the symmetric codec contract.

A codec is a pure two-way translator between wire bytes and Python values.
`decode` was always there; `encode` was added so that `Scenario.publish` and
`Harness.publish` can accept structured payloads without forcing the caller
to hand-concatenate JSON bytes.
"""

from __future__ import annotations

import json

import pytest
from choreo.codecs import JSONCodec, RawCodec

# ---------------------------------------------------------------------------
# JSONCodec
# ---------------------------------------------------------------------------


def test_a_json_codec_should_encode_a_dict_to_utf8_json_bytes() -> None:
    codec = JSONCodec()
    wire = codec.encode({"status": "APPROVED", "qty": 1000})
    assert isinstance(wire, bytes)
    assert json.loads(wire) == {"status": "APPROVED", "qty": 1000}


def test_a_json_codec_should_round_trip_a_value_through_encode_then_decode() -> None:
    codec = JSONCodec()
    payload = {"correlation_id": "TEST-abc", "nested": {"k": [1, 2, 3]}}
    assert codec.decode(codec.encode(payload)) == payload


def test_a_json_codec_should_refuse_to_encode_bytes() -> None:
    """Bytes mean the caller already serialised — they should not reach the
    codec. Surfacing a clear TypeError beats silently double-encoding."""
    codec = JSONCodec()
    with pytest.raises(TypeError, match="bytes"):
        codec.encode(b"already serialised")


def test_a_json_codec_should_emit_no_extraneous_whitespace() -> None:
    """Wire format is compact; tests that assert on byte content should not
    have to account for default `json.dumps` spacing."""
    codec = JSONCodec()
    wire = codec.encode({"a": 1, "b": 2})
    assert b" " not in wire


# ---------------------------------------------------------------------------
# RawCodec
# ---------------------------------------------------------------------------


def test_a_raw_codec_should_pass_bytes_through_on_encode() -> None:
    codec = RawCodec()
    assert codec.encode(b"\x01\x02\x03") == b"\x01\x02\x03"


def test_a_raw_codec_should_accept_a_bytearray_and_return_immutable_bytes() -> None:
    codec = RawCodec()
    out = codec.encode(bytearray(b"abc"))
    assert out == b"abc"
    assert isinstance(out, bytes)


def test_a_raw_codec_should_refuse_to_encode_a_dict() -> None:
    """RawCodec is the explicit opt-out from structured payloads. Encoding a
    dict through it would silently lose information."""
    codec = RawCodec()
    with pytest.raises(TypeError, match="bytes"):
        codec.encode({"k": "v"})

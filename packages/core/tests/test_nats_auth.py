"""Per-variant descriptor tests for NatsAuth.

Each variant is a frozen dataclass.  Tests cover construction, repr safety,
immutability, pickle refusal, deepcopy refusal, identity-only equality
(eq=False), and the __init_subclass__ guard.  See ADR-0020 §Validation.
"""

from __future__ import annotations

import copy
import pickle
from pathlib import Path

import pytest
from choreo.transports.nats_auth import (
    NatsAuth,
    _NatsUserPassword,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TLS = NatsAuth.tls(ca="/path/to/ca.pem")


def _all_variants():
    """Return (name, descriptor) pairs for parametrisation."""
    return [
        ("user_password", NatsAuth.user_password("u", "p")),
        ("token", NatsAuth.token("t")),
        ("nkey", NatsAuth.nkey("SEED")),
        ("credentials_file", NatsAuth.credentials_file("/path/to/creds")),
        ("tls", NatsAuth.tls(ca="/ca.pem")),
        ("user_password_with_tls", NatsAuth.user_password_with_tls("u", "p", _TLS)),
        ("token_with_tls", NatsAuth.token_with_tls("t", _TLS)),
        ("nkey_with_tls", NatsAuth.nkey_with_tls("SEED", _TLS)),
        ("credentials_file_with_tls", NatsAuth.credentials_file_with_tls("/creds", _TLS)),
    ]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, descriptor", _all_variants(), ids=[n for n, _ in _all_variants()])
def test_a_descriptor_should_accept_its_documented_fields(name: str, descriptor: object) -> None:
    assert descriptor is not None


def test_a_user_password_descriptor_should_store_both_fields() -> None:
    d = NatsAuth.user_password("admin", "s3cret")
    assert d.username == "admin"
    assert d.password == "s3cret"


def test_a_token_descriptor_should_store_the_token() -> None:
    d = NatsAuth.token("my-token")
    assert d.token == "my-token"


def test_an_nkey_descriptor_should_accept_bytearray_secret_values() -> None:
    d = NatsAuth.nkey(bytearray(b"SEED"))
    assert d.seed == bytearray(b"SEED")


def test_a_credentials_file_descriptor_should_coerce_to_path() -> None:
    d = NatsAuth.credentials_file("/some/path.creds")
    assert d.path == Path("/some/path.creds")


# ---------------------------------------------------------------------------
# Repr safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, descriptor", _all_variants(), ids=[n for n, _ in _all_variants()])
def test_a_descriptor_repr_should_not_contain_any_field_value(
    name: str, descriptor: object
) -> None:
    r = repr(descriptor)
    # Should show the variant tag only
    assert "<redacted>" in r
    # None of the test secrets should appear
    for secret in ("s3cret", "my-token", "SEED", "admin", "/path/to", "/ca.pem"):
        assert secret not in r


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, descriptor", _all_variants(), ids=[n for n, _ in _all_variants()])
def test_a_descriptor_should_not_permit_mutation_after_construction(
    name: str, descriptor: object
) -> None:
    with pytest.raises(AttributeError):
        descriptor.some_new_field = "oops"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Pickle refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, descriptor", _all_variants(), ids=[n for n, _ in _all_variants()])
def test_a_descriptor_should_refuse_to_pickle(name: str, descriptor: object) -> None:
    with pytest.raises(TypeError, match="pickling"):
        pickle.dumps(descriptor)


# ---------------------------------------------------------------------------
# Deepcopy refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, descriptor", _all_variants(), ids=[n for n, _ in _all_variants()])
def test_a_descriptor_should_refuse_to_deepcopy(name: str, descriptor: object) -> None:
    with pytest.raises(TypeError, match="deepcopy"):
        copy.deepcopy(descriptor)


# ---------------------------------------------------------------------------
# eq=False (identity comparison only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, descriptor", _all_variants(), ids=[n for n, _ in _all_variants()])
def test_two_distinct_descriptors_with_equal_fields_should_not_compare_equal(
    name: str, descriptor: object
) -> None:
    # Construct a second descriptor with the same fields
    other_variants = dict(_all_variants())
    other = other_variants[name]
    # They should NOT be equal even though fields are identical
    assert descriptor is not other
    assert descriptor != other


# ---------------------------------------------------------------------------
# __init_subclass__ guard
# ---------------------------------------------------------------------------


def test_a_descriptor_class_should_refuse_subclasses_declared_outside_the_library_package() -> None:
    with pytest.raises(TypeError, match="cannot subclass _TransportAuth"):

        class Sneaky(_NatsUserPassword):  # type: ignore[misc]
            pass

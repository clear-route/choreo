"""Transport authentication base types and helpers.

Every real transport accepts an ``auth=`` kwarg whose value is either a
concrete auth descriptor (a frozen dataclass subclass of ``_TransportAuth``),
a sync callable returning one, or an async callable returning one.  The
resolver form is invoked inside ``connect()`` so credentials can be fetched
from Vault / Secrets Manager / env-vars at the last possible moment.

**Private-unstable exports.** ``_CREDENTIAL_KEY_NAMES`` and
``_ALLOWED_VARIANTS`` may grow in minor releases.  Consumer tests that pin
their contents are unsupported.

See ADR-0020 for the full design rationale.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeAlias, Union

from .base import TransportError

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

AuthResolver: TypeAlias = Callable[[], "_TransportAuth"]
AsyncAuthResolver: TypeAlias = Callable[[], Awaitable["_TransportAuth"]]
AuthParam: TypeAlias = Union["_TransportAuth", AuthResolver, AsyncAuthResolver, None]

# ---------------------------------------------------------------------------
# Credential key names (canonical, case-folded)
# ---------------------------------------------------------------------------

_CREDENTIAL_KEY_NAMES: frozenset[str] = frozenset(
    {
        "password",
        "token",
        "secret",
        "key",
        "auth",
        "credential",
        "credentials",
        "username",
        "user",
    }
)

# ---------------------------------------------------------------------------
# Variant registry — populated by each *_auth.py module at import time
# ---------------------------------------------------------------------------

_ALLOWED_VARIANTS: dict[str, frozenset[type]] = {}


def _register_variants(transport_name: str, variants: frozenset[type]) -> None:
    """Register the set of concrete auth types a transport accepts."""
    _ALLOWED_VARIANTS[transport_name] = variants


# ---------------------------------------------------------------------------
# Base descriptor
# ---------------------------------------------------------------------------


class _TransportAuth:
    """Abstract base for per-transport auth descriptors.

    Every concrete variant is a ``@dataclass(frozen=True, eq=False, slots=True)``
    subclass with secret-bearing fields declared ``field(repr=False)``.

    Security contract:
    - ``__repr__`` prints only the variant tag (``ClassName(<redacted>)``).
    - ``__reduce__`` and ``__deepcopy__`` raise ``TypeError``.
    - ``eq=False`` means identity comparison only, blocking pytest's
      assertion-rewrite introspection of field values.
    - ``_consumed`` tracks whether ``_clear_auth_fields`` has run.
    """

    _consumed: bool = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        mod = getattr(cls, "__module__", "")
        if not (mod.startswith("choreo.transports.") and mod.endswith("_auth")):
            raise TypeError(
                f"{cls.__qualname__} cannot subclass _TransportAuth: "
                f"module {mod!r} is not a choreo.transports.*_auth module"
            )
        # Subclasses must not override __repr__ — the base implementation
        # is the only one permitted.
        if "__repr__" in cls.__dict__:
            raise TypeError(
                f"{cls.__qualname__} must not define __repr__; "
                "the base _TransportAuth.__repr__ is the only permitted implementation"
            )

    def __repr__(self) -> str:
        return f"{type(self).__qualname__}(<redacted>)"

    def __reduce__(self) -> None:  # type: ignore[override]
        raise TypeError(f"{type(self).__qualname__} does not support pickling")

    def __deepcopy__(self, memo: dict) -> None:  # type: ignore[override]
        raise TypeError(f"{type(self).__qualname__} does not support deepcopy")


# ---------------------------------------------------------------------------
# ConflictingAuthError
# ---------------------------------------------------------------------------


class ConflictingAuthError(TransportError):
    """Raised when a transport detects both URL-carried and explicit auth."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_auth_fields(descriptor: _TransportAuth) -> None:
    """Zero or drop every field on *descriptor*, then mark it consumed.

    - ``bytearray`` / ``memoryview`` fields are zeroed in place.
    - ``bytes`` and ``str`` fields are set to ``None`` (immutable in CPython,
      cannot be zeroed).
    - All other fields are set to ``None``.

    Uses ``object.__setattr__`` to bypass frozen dataclass restrictions.
    """
    if not dataclasses.is_dataclass(descriptor):
        object.__setattr__(descriptor, "_consumed", True)
        return

    for f in dataclasses.fields(descriptor):
        val = getattr(descriptor, f.name, None)
        if isinstance(val, bytearray):
            for i in range(len(val)):
                val[i] = 0
        elif isinstance(val, memoryview):
            for i in range(len(val)):
                val[i] = 0
        # For frozen dataclasses we need object.__setattr__
        try:
            object.__setattr__(descriptor, f.name, None)
        except (AttributeError, TypeError):
            pass
    object.__setattr__(descriptor, "_consumed", True)


def _sanitise_resolver_failure(exc: BaseException) -> str:
    """Return a safe string identifying the exception class only.

    Exotic characters (from dynamically-created exception types) are escaped
    so the result is safe for log formatters and XML report writers.
    """
    qualname = type(exc).__qualname__
    return re.sub(r"[^\w.]", "_", qualname)


async def _resolve_auth(
    raw: AuthParam,
    transport_name: str,
) -> _TransportAuth | None:
    """Resolve the ``auth=`` kwarg into a validated descriptor.

    Accepts a literal descriptor, a sync callable, or an async callable.
    Validates the resolved descriptor against the variant allowlist and
    checks the ``_consumed`` flag.

    Returns ``None`` when *raw* is ``None``.
    """
    if raw is None:
        return None

    # --- resolve callable form ---
    if isinstance(raw, _TransportAuth):
        descriptor = raw
    else:
        # It's a callable (sync or async).
        try:
            result = raw()  # type: ignore[operator]
            if asyncio.iscoroutine(result):
                descriptor = await result
            else:
                descriptor = result  # type: ignore[assignment]
        except Exception as exc:
            cause_name = _sanitise_resolver_failure(exc)
            te = TransportError("auth resolver failed")
            te.resolver_cause = cause_name  # type: ignore[attr-defined]
            te.__suppress_context__ = True
            raise te from None

    # --- variant allowlist check ---
    allowed = _ALLOWED_VARIANTS.get(transport_name)
    if allowed is None:
        raise TransportError(f"no auth variants registered for transport {transport_name!r}")
    if type(descriptor) not in allowed:
        raise TransportError("auth descriptor is not a known variant")

    # --- reuse check ---
    if descriptor._consumed:
        raise TransportError("auth descriptor has already been consumed by another connect()")

    return descriptor

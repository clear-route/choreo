"""Choreo — public API.

The public surface is small by design:

    from core import Harness, BundleFn
    from core.transports import MockTransport            # or LbmTransport etc. (future)
    from core.codecs import JSONCodec, RawCodec, Codec
    from core.environment import Allowlist, load_allowlist
    from core.matchers import field_equals, all_of, ...

Everything else is internal."""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

from .harness import Harness
from .scenario import CorrelationIdNotInTestNamespaceError, Scenario


# Bundle contract exported for consumer-side bundle type-checking.
# A bundle is a plain function that takes a Scenario and registers replies
# on it. Consumers annotating against `BundleFn` opt into mypy-strict
# compatibility with the framework's reply API.
BundleFn: TypeAlias = Callable[[Scenario], None]


__all__ = [
    "BundleFn",
    "CorrelationIdNotInTestNamespaceError",
    "Harness",
    "Scenario",
]

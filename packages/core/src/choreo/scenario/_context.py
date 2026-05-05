"""Per-scope mutable state: state-flag constants, expectation record, context.

Held here so `_dispatch` and `_scope` can collaborate on the same context
type without circular imports.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from choreo.matchers import Matcher

from ._handle import Handle
from ._reply import _Reply
from ._timeline import _Timeline

if TYPE_CHECKING:
    from choreo.harness import Harness


_STATE_BUILDER = "builder"
_STATE_EXPECTING = "expecting"
_STATE_TRIGGERED = "triggered"


@dataclass
class _Expectation:
    handle: Handle
    matcher: Matcher
    registered_at: float
    fulfilled: asyncio.Future[None]


@dataclass
class _ScenarioContext:
    name: str
    harness: Harness
    correlation_id: str | None
    expectations: list[_Expectation] = field(default_factory=list)
    replies: list[_Reply] = field(default_factory=list)
    subscriber_refs: list[tuple[str, Any]] = field(default_factory=list)
    timeline: _Timeline = field(default_factory=_Timeline)
    emitted: bool = False  # set True once `_do_await_all` fires the observer

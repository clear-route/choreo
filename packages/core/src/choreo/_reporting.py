"""Observer seam for the test-report writer — PRD-007 §2.

This is the ONLY library-surface contact point between `core` and the
external `choreo-reporter` package. The reporter registers a callback here;
the scenario runtime calls `_emit(...)` at two points:

  - After `_do_await_all` returns a `ScenarioResult` (the normal path;
    `completed_normally=True`).
  - From `_ScenarioScope.__aexit__` when the scope body raised before
    `_do_await_all` ran (partial capture; `completed_normally=False`).

A contextvar `current_test_nodeid` carries the pytest nodeid into the
observer when the reporter is driving the run. `core` never reads it,
never sets it, and never asserts its presence; it is a hand-off channel
owned by the plugin layer.

Observer errors never propagate. A faulty reporter cannot break tests.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scenario import ScenarioResult


Observer = Callable[["ScenarioResult", "str | None", bool], None]


# The pytest plugin sets this at the start of each test and clears it at
# teardown; `core` only reads it when emitting. Default is None so the
# library functions correctly when no reporter is attached.
current_test_nodeid: ContextVar[str | None] = ContextVar(
    "harness_current_test_nodeid", default=None
)


_observers: list[Observer] = []


def register_observer(cb: Observer) -> None:
    """Register a callback invoked on every scenario completion.

    The callback receives (result, nodeid, completed_normally). The nodeid
    is the value of `current_test_nodeid` at emit time, or None when no
    test context is active. `completed_normally` is True when the emission
    came from `_do_await_all`; False when it came from a scope that exited
    via an unhandled exception before `await_all` ran.

    Observers are called synchronously from the scenario code path; a
    blocking observer blocks the scenario. Exceptions raised inside an
    observer are swallowed via `warnings.warn(...)` — the reporter is
    never a test-failure source (PRD-007 §8).
    """
    if cb not in _observers:
        _observers.append(cb)


def unregister_observer(cb: Observer) -> None:
    """Remove a previously-registered observer. No-op if not present."""
    try:
        _observers.remove(cb)
    except ValueError:
        pass


def _emit(result: ScenarioResult, *, completed_normally: bool) -> None:
    """Notify every registered observer. Internal; called from scenario.py."""
    if not _observers:
        return
    nodeid = current_test_nodeid.get()
    for cb in list(_observers):
        try:
            cb(result, nodeid, completed_normally)
        except Exception as e:
            warnings.warn(
                f"scenario observer {cb!r} raised {type(e).__name__}: {e}; "
                f"observer errors are swallowed to protect the test run",
                RuntimeWarning,
                stacklevel=2,
            )

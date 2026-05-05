"""Scenario DSL — Handles, runtime state checks, scope lifecycle, timeouts.

The package re-exports the same names the pre-package `choreo.scenario`
module exposed; consumers continue to write `from choreo.scenario import X`
without any change.

Submodules:
    _outcome   — `Outcome` enum
    _timeline  — `TimelineAction`, `TimelineEntry`, `_Timeline`
    _handle    — `Handle`
    _reply     — reply types, lifecycle states, freeze/derive helpers
    _result    — `ScenarioResult` + `_diagnose`
    _context   — state-flag constants, `_Expectation`, `_ScenarioContext`
    _dispatch  — subscriber registration, policy wrapping, `_await_all`
    _scope     — `Scenario`, `ReplyChain`, `_ScenarioScope`
"""

from __future__ import annotations

from ._handle import _FAILURES_MAX, Handle
from ._outcome import Outcome
from ._reply import (
    ReplyAlreadyBoundError,
    ReplyReport,
    ReplyReportState,
    _Reply,
    _ReplyState,
)
from ._result import ScenarioResult, _diagnose
from ._scope import ReplyChain, Scenario, _ScenarioScope
from ._timeline import (
    _TIMELINE_DETAIL_MAX_CHARS,
    _TIMELINE_MAX_ENTRIES,
    TimelineAction,
    TimelineEntry,
    _Timeline,
)

# Internal symbols (leading underscore) are intentionally re-exported because
# the test suite imports them directly via `from choreo.scenario import ...`.
__all__ = [
    "_FAILURES_MAX",
    "_TIMELINE_DETAIL_MAX_CHARS",
    "_TIMELINE_MAX_ENTRIES",
    "Handle",
    "Outcome",
    "ReplyAlreadyBoundError",
    "ReplyChain",
    "ReplyReport",
    "ReplyReportState",
    "Scenario",
    "ScenarioResult",
    "TimelineAction",
    "TimelineEntry",
    "_Reply",
    "_ReplyState",
    "_ScenarioScope",
    "_Timeline",
    "_diagnose",
]

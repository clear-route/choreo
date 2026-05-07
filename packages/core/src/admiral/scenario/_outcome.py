"""Handle outcome states (PRD-006, ADR-0014)."""

from __future__ import annotations

from enum import StrEnum


class Outcome(StrEnum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    SLOW = "slow"

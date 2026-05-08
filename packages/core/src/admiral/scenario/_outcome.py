"""Handle outcome states."""

from __future__ import annotations

from enum import StrEnum


class Outcome(StrEnum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    SLOW = "slow"

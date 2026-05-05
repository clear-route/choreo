"""Normalise tests for v1.1 reports (PRD-012).

Asserts Chronicle's `normalise_report` accepts the new shape without
KeyError / type errors:

* `run.transport` may be null when `run.transports` is populated.
* `run.transports` carries the sorted union of transport names.
* `handle.transport` is preserved per-handle.
* `scenario.stage` is tolerated (passed through; not yet split into
  separate columns).
"""

from __future__ import annotations

from typing import Any

import pytest
from chronicle.schemas.ingest import IngestRequest
from chronicle.services.normalise import normalise_report


def _minimal_run(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "started_at": "2026-05-04T10:00:00Z",
        "finished_at": "2026-05-04T10:00:01Z",
        "duration_ms": 1.0,
        "totals": {
            "passed": 0,
            "failed": 0,
            "errored": 0,
            "skipped": 0,
            "slow": 0,
            "total": 0,
        },
        "project_name": "demo",
        "transport": "MockTransport",
        "allowlist_path": "config/allowlist.yaml",
        "python_version": "3.13",
        "harness_version": "0.1.0",
        "reporter_version": "0.2.0",
        "git_sha": None,
        "git_branch": None,
        "environment": "dev",
        "hostname": "test",
        "xdist": None,
        "truncated": False,
        "redactions": {"fields": 0, "stream_matches": 0, "redaction_version": "v1"},
    }
    base.update(overrides)
    return base


def _make_request(run: dict[str, Any], tests: list[dict[str, Any]] | None = None) -> IngestRequest:
    return IngestRequest(schema_version="1.1", run=run, tests=tests or [])


def test_normalise_should_accept_null_transport_when_transports_is_present():
    """PRD-012: Stage-only runs emit `transport: null`, `transports: [...]`."""
    run = _minimal_run(transport=None, transports=["kafka", "nats"])
    req = _make_request(run)
    norm = normalise_report(req)
    assert norm.transport is None
    assert norm.transports == ["kafka", "nats"]


def test_normalise_should_omit_transports_when_only_run_transport_present():
    """Single-Harness runs: `transport` carries the class name; no `transports`."""
    req = _make_request(_minimal_run())
    norm = normalise_report(req)
    assert norm.transport == "MockTransport"
    assert norm.transports is None


def test_normalise_should_accept_both_transport_and_transports_for_mixed_runs():
    """Mixed runs emit transport=null + transports=union."""
    run = _minimal_run(transport=None, transports=["kafka", "nats"])
    req = _make_request(run)
    norm = normalise_report(req)
    assert norm.transport is None
    assert norm.transports == ["kafka", "nats"]


def test_normalise_should_preserve_per_handle_transport_for_stage_scenarios():
    """A Stage handle's `transport` field flows into NormalisedHandle so the
    repository can write it to `handle_measurements.transport`."""
    handle = {
        "topic": "results",
        "outcome": "pass",
        "latency_ms": 5.0,
        "budget_ms": None,
        "matcher_description": "any",
        "expected": None,
        "actual": None,
        "attempts": 1,
        "reason": "matched",
        "truncated": False,
        "failure": None,
        "failures": [],
        "failures_dropped": 0,
        "diagnosis": {"kind": "matched"},
        "transport": "nats",
    }
    test = {
        "nodeid": "t.py::t",
        "file": "t.py",
        "name": "t",
        "class": None,
        "markers": [],
        "choreo_meta": None,
        "outcome": "passed",
        "duration_ms": 5.0,
        "traceback": None,
        "stdout": "",
        "stderr": "",
        "log": "",
        "skip_reason": None,
        "worker_id": None,
        "scenarios": [
            {
                "name": "stage",
                "correlation_id": "logical-x",
                "outcome": "pass",
                "duration_ms": 5.0,
                "completed_normally": True,
                "handles": [handle],
                "timeline": [],
                "timeline_dropped": 0,
                "replies": [],
                "summary_text": "",
                "stage": {
                    "bridge_class": "MappedBridge",
                    "transports": ["kafka", "nats"],
                    "correlation_ids": {
                        "nats": "sha256:0123456789abcdef",
                        "kafka": "sha256:fedcba9876543210",
                    },
                },
            }
        ],
    }
    run = _minimal_run(transport=None, transports=["kafka", "nats"])
    req = _make_request(run, tests=[test])

    norm = normalise_report(req)
    assert len(norm.scenarios) == 1
    assert len(norm.scenarios[0].handles) == 1
    assert norm.scenarios[0].handles[0].transport == "nats"


def test_a_stage_scenario_with_a_handle_missing_transport_should_raise_value_error():
    """PRD-012 §1.1: a Stage handle MUST carry a `transport` field.
    Failing fast at normalise prevents Chronicle's NOT NULL constraint
    on `handle_measurements.transport` from raising an opaque
    IntegrityError mid-COPY."""
    bad_handle = {
        "topic": "results",
        "outcome": "pass",
        "latency_ms": 5.0,
        "budget_ms": None,
        "matcher_description": "any",
        "expected": None,
        "actual": None,
        "attempts": 1,
        "reason": "matched",
        "truncated": False,
        "failure": None,
        "failures": [],
        "failures_dropped": 0,
        "diagnosis": {"kind": "matched"},
        # `transport` deliberately absent — the scenario carries a
        # `stage` block (signalling Stage) so the handle MUST have it.
    }
    test = {
        "nodeid": "t.py::t",
        "file": "t.py",
        "name": "t",
        "class": None,
        "markers": [],
        "choreo_meta": None,
        "outcome": "passed",
        "duration_ms": 5.0,
        "traceback": None,
        "stdout": "",
        "stderr": "",
        "log": "",
        "skip_reason": None,
        "worker_id": None,
        "scenarios": [
            {
                "name": "scn",
                "correlation_id": "logical-x",
                "outcome": "pass",
                "duration_ms": 5.0,
                "completed_normally": True,
                "handles": [bad_handle],
                "timeline": [],
                "timeline_dropped": 0,
                "replies": [],
                "summary_text": "",
                "stage": {
                    "bridge_class": "MappedBridge",
                    "transports": ["nats"],
                    "correlation_ids": {"nats": "sha256:0123456789abcdef"},
                },
            }
        ],
    }
    req = _make_request(_minimal_run(transport=None, transports=["nats"]), tests=[test])
    with pytest.raises(ValueError, match="transport"):
        normalise_report(req)


def test_normalise_should_default_handle_transport_to_none_for_single_harness():
    """v1.0-shape handles (no `transport` key) normalise to transport=None."""
    handle = {
        "topic": "results",
        "outcome": "pass",
        "latency_ms": 5.0,
        "budget_ms": None,
        "matcher_description": "any",
        "expected": None,
        "actual": None,
        "attempts": 1,
        "reason": "matched",
        "truncated": False,
        "failure": None,
        "failures": [],
        "failures_dropped": 0,
        "diagnosis": {"kind": "matched"},
    }
    test = {
        "nodeid": "t.py::t",
        "file": "t.py",
        "name": "t",
        "class": None,
        "markers": [],
        "choreo_meta": None,
        "outcome": "passed",
        "duration_ms": 5.0,
        "traceback": None,
        "stdout": "",
        "stderr": "",
        "log": "",
        "skip_reason": None,
        "worker_id": None,
        "scenarios": [
            {
                "name": "scn",
                "correlation_id": "x",
                "outcome": "pass",
                "duration_ms": 5.0,
                "completed_normally": True,
                "handles": [handle],
                "timeline": [],
                "timeline_dropped": 0,
                "replies": [],
                "summary_text": "",
            }
        ],
    }
    req = _make_request(_minimal_run(), tests=[test])
    norm = normalise_report(req)
    assert norm.scenarios[0].handles[0].transport is None

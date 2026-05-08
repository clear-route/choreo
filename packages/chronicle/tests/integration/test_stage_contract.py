""" Phase 1 contract test: reporter → Chronicle round-trip.

The contract under test is the Phase 1 exit gate ( Implementation
Plan §Phase 1): a v1.1 Stage report emitted by the admiral-reporter
must pass Chronicle's ingest pipeline (Pydantic level 1, then
normalisation, then repository writes) without 4xx or 5xx errors,
preserving per-handle transport for downstream queries.

Two surfaces:

* **No-DB integration** (this file, `chronicle_db`-marker-free): drives
  the report through Pydantic + ``normalise_report`` to assert the
  shape is accepted at the validation layers. Runs in every CI pass.
* **DB-backed e2e** (``e2e/test_stage_contract_db.py``, marked
  ``chronicle_db``): POSTs the report to a running Chronicle instance
  and asserts 201 + `runs.transports` is populated. Runs only when
  TimescaleDB is reachable.
"""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest
from chronicle.schemas.ingest import IngestRequest
from chronicle.services.normalise import normalise_report


def _v1_1_stage_report() -> dict[str, Any]:
    """A minimal v1.1 Stage report mirroring  Appendix A.3.

    Stage-only: `run.transport` is null, `run.transports` carries the
    sorted union; one Stage scenario with two transports and one Stage
    handle whose `correlation_id` is hash-redacted.
    """
    handle = {
        "topic": "results",
        "outcome": "pass",
        "latency_ms": 41.7,
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
        "correlation_id": "sha256:3f2a91b8c4d50e1f",
    }
    scenario = {
        "name": "bridge",
        "correlation_id": "logical-3f2a91b8c4d50e1f",
        "outcome": "pass",
        "duration_ms": 12.5,
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
                "nats": "sha256:3f2a91b8c4d50e1f",
                "kafka": "sha256:7e8b50c1ad4912ff",
            },
        },
    }
    test = {
        "nodeid": "tests/test_bridge.py::test_round_trip",
        "file": "tests/test_bridge.py",
        "name": "test_round_trip",
        "class": None,
        "markers": [],
        "admiral_meta": None,
        "outcome": "passed",
        "duration_ms": 12.5,
        "traceback": None,
        "stdout": "",
        "stderr": "",
        "log": "",
        "skip_reason": None,
        "worker_id": None,
        "scenarios": [scenario],
    }
    return {
        "schema_version": "1.1",
        "run": {
            "started_at": "2026-05-04T10:00:00Z",
            "finished_at": "2026-05-04T10:00:01Z",
            "duration_ms": 1.0,
            "totals": {
                "passed": 1,
                "failed": 0,
                "errored": 0,
                "skipped": 0,
                "slow": 0,
                "total": 1,
            },
            "project_name": "demo",
            "transport": None,
            "transports": ["kafka", "nats"],
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
            "redactions": {
                "fields": 0,
                "stream_matches": 0,
                "redaction_version": "v1",
            },
        },
        "tests": [test],
    }


# ---------------------------------------------------------------------------
# Level 1: Pydantic validation
# ---------------------------------------------------------------------------


def test_a_v1_1_stage_report_should_pass_pydantic_validation():
    """The reporter's v1.1 output must satisfy Chronicle's
    `IngestRequest` Pydantic schema."""
    report = _v1_1_stage_report()
    req = IngestRequest(**report)
    assert req.schema_version == "1.1"
    assert req.run["transport"] is None
    assert req.run["transports"] == ["kafka", "nats"]


# ---------------------------------------------------------------------------
# Level 2: JSON Schema validation (against the published v1.1 schema)
# ---------------------------------------------------------------------------


def test_a_v1_1_stage_report_should_validate_against_test_report_v1_1_schema(
    schema_v1_1: dict,
) -> None:
    """The contract is bidirectional: any report Chronicle accepts must
    also satisfy the published JSON Schema."""
    report = _v1_1_stage_report()
    jsonschema.Draft202012Validator(schema_v1_1).validate(report)


# ---------------------------------------------------------------------------
# Level 3: normalisation produces the expected NormalisedReport shape
# ---------------------------------------------------------------------------


def test_a_v1_1_stage_report_should_normalise_with_per_handle_transport_preserved():
    """End-to-end of the validation/normalisation pipeline. Asserts:
    - `transport` flows through as None (Stage-only).
    - `transports` is preserved as the sorted union.
    - Per-handle `transport` lands on `NormalisedHandle.transport`.
    """
    report = _v1_1_stage_report()
    req = IngestRequest(**report)
    norm = normalise_report(req)
    assert norm.transport is None
    assert norm.transports == ["kafka", "nats"]
    assert len(norm.scenarios) == 1
    assert norm.scenarios[0].handles[0].transport == "nats"


# ---------------------------------------------------------------------------
# Schema fixture (loaded via conftest)
# ---------------------------------------------------------------------------


@pytest.fixture()
def schema_v1_1() -> dict:
    import json
    from pathlib import Path

    schema_path = Path(__file__).resolve().parents[4] / "docs" / "schemas" / "test-report-v1.1.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))

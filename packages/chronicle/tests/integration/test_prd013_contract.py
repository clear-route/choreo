"""PRD-013 Phase 3 PR 3.2 contract test: reporter v1.3 → Chronicle round-trip.

Mirrors `test_prd012_contract.py`'s pattern but for the v1.3 schema
which adds `timeline_entry.source` (PRD-013 §1.6) on top of v1.2's
`transport` / `logical_topic` / optional `topic`. The contract under
test: a v1.3 Stage report emitted by the choreo-reporter must pass
Chronicle's ingest pipeline (Pydantic level 1 + JSON Schema level 2
+ normalisation) without 4xx or 5xx errors, with the optional
v1.2/v1.3 fields flowing through unchanged.

Two surfaces:

* **No-DB integration** (this file): drives the report through
  Pydantic + JSON Schema + `normalise_report` to assert the shape
  is accepted. Runs in every CI pass.
* **DB-backed e2e** (Phase 3 PR 3.3 — `e2e/test_prd013_timeline_db.py`):
  POSTs the report to a running Chronicle, asserts the
  `timeline_events` hypertable is populated.
"""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest
from chronicle.schemas.ingest import IngestRequest
from chronicle.services.normalise import normalise_report


def _v1_3_stage_report() -> dict[str, Any]:
    """A minimal v1.3 Stage report exercising every new optional field
    and every TimelineAction value.

    Fields exercised:
    - `schema_version: "1.3"` (PRD-013)
    - `timeline_entry.transport` (v1.2)
    - `timeline_entry.topic` optional (v1.2; DEADLINE omits)
    - `timeline_entry.logical_topic` (v1.2; populated for one entry)
    - `timeline_entry.source` (v1.3 enum: publish/expect/reply/scope)
    """
    return {
        "schema_version": "1.3",
        "run": {
            "started_at": "2026-05-05T10:00:00+00:00",
            "finished_at": "2026-05-05T10:00:01+00:00",
            "duration_ms": 1000.0,
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
        "tests": [
            {
                "nodeid": "tests/test_bridge.py::test_round_trip",
                "file": "tests/test_bridge.py",
                "name": "test_round_trip",
                "class": None,
                "markers": [],
                "choreo_meta": None,
                "outcome": "passed",
                "duration_ms": 12.5,
                "traceback": None,
                "stdout": "",
                "stderr": "",
                "log": "",
                "skip_reason": None,
                "worker_id": None,
                "scenarios": [
                    {
                        "name": "bridge",
                        "correlation_id": "logical-x",
                        "outcome": "pass",
                        "duration_ms": 12.5,
                        "completed_normally": True,
                        "handles": [
                            {
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
                        ],
                        "timeline": [
                            {
                                "offset_ms": 0.0,
                                "wall_clock": "2026-05-05T10:00:00+00:00",
                                "action": "published",
                                "detail": "",
                                "topic": "orders.new",
                                "transport": "kafka",
                                "source": "publish",
                            },
                            {
                                "offset_ms": 0.5,
                                "wall_clock": "2026-05-05T10:00:00.0005+00:00",
                                "action": "received",
                                "detail": "",
                                "topic": "orders.new",
                                "transport": "kafka",
                                "source": "reply",
                            },
                            {
                                "offset_ms": 1.0,
                                "wall_clock": "2026-05-05T10:00:00.001+00:00",
                                "action": "replied",
                                "detail": "trigger=orders.new",
                                "topic": "nats-orders.processed",
                                "transport": "nats",
                                "logical_topic": "orders.processed",
                                "source": "reply",
                            },
                            {
                                "offset_ms": 1.5,
                                "wall_clock": "2026-05-05T10:00:00.0015+00:00",
                                "action": "received",
                                "detail": "",
                                "topic": "results",
                                "transport": "nats",
                                "source": "expect",
                            },
                            {
                                "offset_ms": 2.0,
                                "wall_clock": "2026-05-05T10:00:00.002+00:00",
                                "action": "matched",
                                "detail": "",
                                "topic": "results",
                                "transport": "nats",
                                "source": "expect",
                            },
                            {
                                "offset_ms": 12.0,
                                "wall_clock": "2026-05-05T10:00:00.012+00:00",
                                "action": "deadline",
                                "detail": "timeout_ms=200",
                                "source": "scope",
                            },
                        ],
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
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Level 1: Pydantic structural validation (already permissive on 1.x)
# ---------------------------------------------------------------------------


def test_a_v1_3_stage_report_should_pass_pydantic_validation():
    report = _v1_3_stage_report()
    req = IngestRequest(**report)
    assert req.schema_version == "1.3"
    assert req.run["transports"] == ["kafka", "nats"]


# ---------------------------------------------------------------------------
# Level 2: JSON Schema validation against the published v1.3 schema
# ---------------------------------------------------------------------------


def test_a_v1_3_stage_report_should_validate_against_test_report_v1_3_schema(
    schema_v1_3: dict,
) -> None:
    """Bidirectional contract: any report Chronicle accepts must also
    satisfy the published JSON Schema."""
    report = _v1_3_stage_report()
    jsonschema.Draft202012Validator(schema_v1_3).validate(report)


def test_a_v1_3_timeline_entry_with_unknown_source_should_fail_schema_validation(
    schema_v1_3: dict,
) -> None:
    """The v1.3 schema constrains `source` to the four-value enum
    (publish/expect/reply/scope). A consumer that emits a malformed
    value gets rejected at the schema layer, surfacing the bug early."""
    report = _v1_3_stage_report()
    report["tests"][0]["scenarios"][0]["timeline"][0]["source"] = "invalid"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema_v1_3).validate(report)


def test_a_v1_3_timeline_entry_omitting_optional_source_should_validate(
    schema_v1_3: dict,
) -> None:
    """The `source` field is optional (PRD-013 §1.6); single-Harness
    entries pre-dating v1.3 instrumentation omit it."""
    report = _v1_3_stage_report()
    for entry in report["tests"][0]["scenarios"][0]["timeline"]:
        entry.pop("source", None)
    jsonschema.Draft202012Validator(schema_v1_3).validate(report)


# ---------------------------------------------------------------------------
# Level 3: normalisation accepts v1.3 reports without errors
# ---------------------------------------------------------------------------


def test_a_v1_3_stage_report_should_normalise_without_errors():
    """v1.3 additions must not regress the PRD-012 per-handle
    transport flow. Per-handle transport still lands on
    `NormalisedHandle.transport`."""
    report = _v1_3_stage_report()
    req = IngestRequest(**report)
    norm = normalise_report(req)
    assert norm.transport is None
    assert norm.transports == ["kafka", "nats"]
    assert len(norm.scenarios) == 1
    assert norm.scenarios[0].handles[0].transport == "nats"


def test_a_v1_3_stage_report_should_extract_timeline_events_into_normalised_scenarios():
    """PRD-013 PR 3.3: `normalise_report` extracts every entry in
    `scenario.timeline[]` into a `NormalisedTimelineEvent`. Optional
    fields (topic / transport / logical_topic / source) flow through
    as None when omitted in the source JSON."""
    report = _v1_3_stage_report()
    req = IngestRequest(**report)
    norm = normalise_report(req)
    timeline = norm.scenarios[0].timeline
    assert len(timeline) == 6
    actions = [e.action for e in timeline]
    assert actions == [
        "published",
        "received",
        "replied",
        "received",
        "matched",
        "deadline",
    ]


def test_a_v1_3_stage_report_should_extract_source_attribution_per_event():
    """PRD-013 §1.6: each timeline event's `source` flows through
    as the corresponding NormalisedTimelineEvent's source."""
    report = _v1_3_stage_report()
    req = IngestRequest(**report)
    norm = normalise_report(req)
    timeline = norm.scenarios[0].timeline
    sources = [e.source for e in timeline]
    assert sources == ["publish", "reply", "reply", "expect", "expect", "scope"]


def test_a_v1_3_deadline_event_should_normalise_with_topic_and_transport_as_none():
    """PRD-013 §D-3: scope-level events (DEADLINE) omit `topic` and
    `transport` in the JSON; the normaliser carries the absence
    through as `None`."""
    report = _v1_3_stage_report()
    req = IngestRequest(**report)
    norm = normalise_report(req)
    timeline = norm.scenarios[0].timeline
    deadlines = [e for e in timeline if e.action == "deadline"]
    assert len(deadlines) == 1
    assert deadlines[0].topic is None
    assert deadlines[0].transport is None


def test_a_v1_3_logical_topic_should_flow_through_when_set():
    """PRD-013 §1.3: `logical_topic` is the cross-transport identity
    for translating bridges; the normaliser preserves it."""
    report = _v1_3_stage_report()
    req = IngestRequest(**report)
    norm = normalise_report(req)
    timeline = norm.scenarios[0].timeline
    replied = next(e for e in timeline if e.action == "replied")
    assert replied.topic == "nats-orders.processed"
    assert replied.logical_topic == "orders.processed"


def test_a_v1_1_report_with_empty_timeline_should_normalise_with_empty_tuple():
    """Backward-compat: v1.0/v1.1 reports emit `timeline: []` for
    scenarios; the normaliser produces an empty tuple, never None."""
    report = _v1_3_stage_report()
    report["schema_version"] = "1.1"  # Pydantic only checks startswith
    report["tests"][0]["scenarios"][0]["timeline"] = []
    req = IngestRequest(**report)
    norm = normalise_report(req)
    assert norm.scenarios[0].timeline == ()


# ---------------------------------------------------------------------------
# Fixture: v1.3 schema loaded from the published file
# ---------------------------------------------------------------------------


@pytest.fixture
def schema_v1_3() -> dict:
    import json
    from pathlib import Path

    schema_path = (
        Path(__file__).resolve().parents[4]
        / "docs"
        / "schemas"
        / "test-report-v1.3.json"
    )
    return json.loads(schema_path.read_text(encoding="utf-8"))

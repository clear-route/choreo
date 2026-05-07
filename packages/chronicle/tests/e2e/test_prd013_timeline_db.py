"""PRD-013 PR 3.3 e2e: timeline_events COPY persistence against TimescaleDB.

Asserts the asyncpg COPY pipeline ships v1.3 timeline entries into
the `timeline_events` hypertable, preserving every optional field
(transport / topic / logical_topic / source) and the action enum.

Skipped cleanly when TimescaleDB is not reachable. Requires migration
005 to have been applied (PRD-013 §5 hypertable creation).
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest
from conftest import skip_no_db
from fastapi.testclient import TestClient

pytestmark = pytest.mark.chronicle_db


def _v1_3_stage_report() -> dict[str, Any]:
    """A minimal v1.3 Stage report with a six-event timeline covering
    every TimelineAction value used by the canonical bridge round-trip
    plus a scope-level DEADLINE."""
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
                "admiral_meta": None,
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


_DB_URL = "postgresql://chronicle:chronicle@localhost:5433/chronicle"


async def _query_timeline_events(run_id: str) -> list[dict]:
    conn = await asyncpg.connect(_DB_URL)
    try:
        rows = await conn.fetch(
            """
            SELECT action, detail, offset_ms, topic, transport,
                   logical_topic, source
            FROM timeline_events
            WHERE run_id = $1
            ORDER BY offset_ms ASC
            """,
            run_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


@skip_no_db
class TestPRD013TimelineIngest:
    """End-to-end: POST a v1.3 Stage report → assert 201 + timeline rows
    land in the `timeline_events` hypertable with all v1.2/v1.3 fields
    preserved."""

    def test_a_v1_3_stage_report_should_persist_timeline_events_in_observation_order(
        self, db_client: TestClient
    ) -> None:
        response = db_client.post(
            "/api/v1/runs",
            json=_v1_3_stage_report(),
            headers={"X-Chronicle-Tenant": "prd013-timeline"},
        )
        assert response.status_code == 201, response.text
        run_id = response.json()["run_id"]

        events = asyncio.run(_query_timeline_events(run_id))
        actions = [e["action"] for e in events]
        assert actions == [
            "published",
            "received",
            "replied",
            "received",
            "matched",
            "deadline",
        ]

    def test_timeline_events_should_carry_per_event_source_attribution(
        self, db_client: TestClient
    ) -> None:
        response = db_client.post(
            "/api/v1/runs",
            json=_v1_3_stage_report(),
            headers={"X-Chronicle-Tenant": "prd013-timeline"},
        )
        assert response.status_code == 201
        run_id = response.json()["run_id"]

        events = asyncio.run(_query_timeline_events(run_id))
        sources = [e["source"] for e in events]
        assert sources == ["publish", "reply", "reply", "expect", "expect", "scope"]

    def test_a_deadline_event_should_persist_with_null_topic_and_transport(
        self, db_client: TestClient
    ) -> None:
        response = db_client.post(
            "/api/v1/runs",
            json=_v1_3_stage_report(),
            headers={"X-Chronicle-Tenant": "prd013-timeline"},
        )
        assert response.status_code == 201
        run_id = response.json()["run_id"]

        events = asyncio.run(_query_timeline_events(run_id))
        deadlines = [e for e in events if e["action"] == "deadline"]
        assert len(deadlines) == 1
        assert deadlines[0]["topic"] is None
        assert deadlines[0]["transport"] is None

    def test_logical_topic_should_persist_when_set(self, db_client: TestClient) -> None:
        response = db_client.post(
            "/api/v1/runs",
            json=_v1_3_stage_report(),
            headers={"X-Chronicle-Tenant": "prd013-timeline"},
        )
        assert response.status_code == 201
        run_id = response.json()["run_id"]

        events = asyncio.run(_query_timeline_events(run_id))
        replied = next(e for e in events if e["action"] == "replied")
        assert replied["topic"] == "nats-orders.processed"
        assert replied["logical_topic"] == "orders.processed"

    def test_a_v1_1_report_with_empty_timeline_should_skip_the_timeline_copy(
        self, db_client: TestClient
    ) -> None:
        """Backward-compat: pre-v1.3 reports emit `timeline: []`. The
        ingest pipeline skips the COPY when there are no events; no
        rows land in `timeline_events`."""
        v1_1_report = _v1_3_stage_report()
        v1_1_report["schema_version"] = "1.1"
        v1_1_report["tests"][0]["scenarios"][0]["timeline"] = []
        response = db_client.post(
            "/api/v1/runs",
            json=v1_1_report,
            headers={"X-Chronicle-Tenant": "prd013-timeline-empty"},
        )
        assert response.status_code == 201
        run_id = response.json()["run_id"]

        events = asyncio.run(_query_timeline_events(run_id))
        assert events == []


@skip_no_db
class TestPRD013TimelineReadAPI:
    """End-to-end: GET /api/v1/runs/{run_id}/timeline returns
    persisted timeline events with optional filters."""

    def _ingest_canonical(self, db_client: TestClient) -> str:
        response = db_client.post(
            "/api/v1/runs",
            json=_v1_3_stage_report(),
            headers={"X-Chronicle-Tenant": "prd013-timeline-read"},
        )
        assert response.status_code == 201, response.text
        return response.json()["run_id"]

    def test_get_timeline_should_return_all_events_in_observation_order(
        self, db_client: TestClient
    ) -> None:
        run_id = self._ingest_canonical(db_client)
        response = db_client.get(f"/api/v1/runs/{run_id}/timeline")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 6
        actions = [e["action"] for e in body["items"]]
        assert actions == [
            "published",
            "received",
            "replied",
            "received",
            "matched",
            "deadline",
        ]

    def test_get_timeline_should_filter_by_transport(self, db_client: TestClient) -> None:
        run_id = self._ingest_canonical(db_client)
        response = db_client.get(f"/api/v1/runs/{run_id}/timeline?transport=kafka")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert all(e["transport"] == "kafka" for e in body["items"])

    def test_get_timeline_should_filter_by_action(self, db_client: TestClient) -> None:
        run_id = self._ingest_canonical(db_client)
        response = db_client.get(f"/api/v1/runs/{run_id}/timeline?action=replied")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["action"] == "replied"
        assert body["items"][0]["topic"] == "nats-orders.processed"

    def test_get_timeline_should_filter_by_source(self, db_client: TestClient) -> None:
        run_id = self._ingest_canonical(db_client)
        response = db_client.get(f"/api/v1/runs/{run_id}/timeline?source=reply")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert all(e["source"] == "reply" for e in body["items"])

    def test_get_timeline_should_paginate_via_limit_and_offset(self, db_client: TestClient) -> None:
        run_id = self._ingest_canonical(db_client)
        # First page of 2
        page1 = db_client.get(f"/api/v1/runs/{run_id}/timeline?limit=2&offset=0")
        assert page1.status_code == 200
        assert page1.json()["total"] == 6
        assert len(page1.json()["items"]) == 2
        # Second page of 2
        page2 = db_client.get(f"/api/v1/runs/{run_id}/timeline?limit=2&offset=2")
        assert page2.status_code == 200
        assert page2.json()["total"] == 6
        assert len(page2.json()["items"]) == 2
        # Third page of 2
        page3 = db_client.get(f"/api/v1/runs/{run_id}/timeline?limit=2&offset=4")
        assert page3.status_code == 200
        assert len(page3.json()["items"]) == 2
        # Page beyond total
        page4 = db_client.get(f"/api/v1/runs/{run_id}/timeline?limit=2&offset=6")
        assert page4.status_code == 200
        assert page4.json()["items"] == []

    def test_get_timeline_should_404_for_unknown_run_id(self, db_client: TestClient) -> None:
        response = db_client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000/timeline")
        assert response.status_code == 404

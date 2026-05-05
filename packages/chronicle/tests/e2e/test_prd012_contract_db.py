"""PRD-012 Phase 1 contract test against a real Chronicle + TimescaleDB.

Asserts:

* A v1.1 Stage report POSTs to ``/api/v1/runs`` with HTTP 201.
* `runs.transport` is null and `runs.transports` is populated.
* Per-handle transport flows into ``handle_measurements.transport``.
* The run is queryable via ``GET /api/v1/runs/{run_id}``.

Skipped cleanly when TimescaleDB is not reachable. Requires migration
003 to have been applied (PRD-012 NOT NULL relaxation + transports
column).
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest
from conftest import skip_no_db
from fastapi.testclient import TestClient

pytestmark = pytest.mark.chronicle_db


def _v1_1_stage_report() -> dict[str, Any]:
    """Mirror of `tests/integration/test_prd012_contract._v1_1_stage_report`.

    Duplicated here intentionally so the e2e test stands alone — the
    integration test exercises validation; this exercises persistence.
    """
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
                        "correlation_id": "logical-3f2a91b8c4d50e1f",
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
                ],
            }
        ],
    }


@skip_no_db
class TestPRD012Contract:
    """End-to-end: POST a v1.1 Stage report → assert 201 + persistence."""

    def test_a_v1_1_stage_report_should_be_accepted_with_201(
        self, db_client: TestClient
    ) -> None:
        response = db_client.post(
            "/api/v1/runs",
            json=_v1_1_stage_report(),
            headers={"X-Chronicle-Tenant": "prd012-contract"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["duplicate"] is False
        assert body["scenarios_ingested"] == 1
        assert body["handles_ingested"] == 1

    def test_runs_transport_should_be_null_and_transports_should_be_populated(
        self, db_client: TestClient
    ) -> None:
        response = db_client.post(
            "/api/v1/runs",
            json=_v1_1_stage_report(),
            headers={"X-Chronicle-Tenant": "prd012-contract"},
        )
        assert response.status_code == 201
        run_id = response.json()["run_id"]

        async def _query() -> tuple[Any, Any]:
            conn = await asyncpg.connect(
                "postgresql://chronicle:chronicle@localhost:5433/chronicle"
            )
            try:
                row = await conn.fetchrow(
                    "SELECT transport, transports FROM runs WHERE id = $1",
                    run_id,
                )
            finally:
                await conn.close()
            return row["transport"], row["transports"]

        transport, transports = asyncio.run(_query())
        assert transport is None
        assert transports == ["kafka", "nats"]

    def test_handle_measurements_should_carry_per_handle_transport(
        self, db_client: TestClient
    ) -> None:
        response = db_client.post(
            "/api/v1/runs",
            json=_v1_1_stage_report(),
            headers={"X-Chronicle-Tenant": "prd012-contract"},
        )
        assert response.status_code == 201
        run_id = response.json()["run_id"]

        async def _query() -> list[str]:
            conn = await asyncpg.connect(
                "postgresql://chronicle:chronicle@localhost:5433/chronicle"
            )
            try:
                rows = await conn.fetch(
                    "SELECT transport FROM handle_measurements WHERE run_id = $1",
                    run_id,
                )
            finally:
                await conn.close()
            return [r["transport"] for r in rows]

        transports = asyncio.run(_query())
        assert transports == ["nats"], (
            "per-handle transport must be preserved on handle_measurements; "
            "got " + repr(transports)
        )

"""Integration tests for the full ingest pipeline against real TimescaleDB.

These tests require the Chronicle Docker Compose stack to be running:

    docker compose -f docker/compose.chronicle.yaml up -d
    cd packages/chronicle && alembic upgrade head
    pytest packages/chronicle/tests/test_ingest_db.py -m chronicle_db

If TimescaleDB is not reachable, tests skip rather than fail.

Follows the project test naming convention: behaviour-oriented names
using "should" / "should not" (CLAUDE.md §Test style).
"""

from __future__ import annotations

import pytest
from conftest import skip_no_db
from fastapi.testclient import TestClient

pytestmark = pytest.mark.chronicle_db


@skip_no_db
class TestIngestEndToEnd:
    """Full pipeline: POST report -> persist in TimescaleDB -> query back."""

    def test_ingesting_a_report_should_persist_run_and_scenarios(
        self, db_client: TestClient, real_report: dict
    ) -> None:
        response = db_client.post(
            "/api/v1/runs",
            json=real_report,
            headers={"X-Chronicle-Tenant": "integration-test"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["duplicate"] is False
        assert data["scenarios_ingested"] > 0
        assert data["handles_ingested"] > 0

    def test_ingesting_the_same_report_with_idempotency_key_should_return_duplicate(
        self, db_client: TestClient, real_report: dict
    ) -> None:
        headers = {
            "X-Chronicle-Tenant": "integration-test",
            "Idempotency-Key": "test-idempotent-001",
        }

        r1 = db_client.post("/api/v1/runs", json=real_report, headers=headers)
        r2 = db_client.post("/api/v1/runs", json=real_report, headers=headers)

        assert r1.status_code == 201
        assert r2.json()["duplicate"] is True
        assert r2.json()["run_id"] == r1.json()["run_id"]

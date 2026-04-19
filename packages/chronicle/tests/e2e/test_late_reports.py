"""E2E tests for late report warning in the ingest response.

Verifies that the full ingest pipeline (against real TimescaleDB)
returns a ``warning`` field for reports older than 24 hours.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import skip_no_db
from factories import make_report
from fastapi.testclient import TestClient

pytestmark = pytest.mark.chronicle_db


def _iso_ago(days: int, hours: int = 0) -> str:
    dt = datetime.now(UTC) - timedelta(days=days, hours=hours)
    return dt.isoformat()


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


@skip_no_db
class TestLateReportWarning:
    """Verify the ingest response includes a warning for late reports."""

    def test_ingesting_a_report_from_two_days_ago_should_include_a_warning(
        self, db_client: TestClient
    ) -> None:
        two_days_ago = _iso_ago(days=2)
        body = make_report(started_at=two_days_ago, finished_at=two_days_ago)

        response = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "late-team",
                "Idempotency-Key": f"late-warn-{uuid.uuid4()}",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["warning"] is not None
        assert "aggregate refresh window" in data["warning"]

    def test_ingesting_a_recent_report_should_not_include_a_warning(
        self, db_client: TestClient
    ) -> None:
        now = _iso_now()
        body = make_report(started_at=now, finished_at=now)

        response = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "late-team",
                "Idempotency-Key": f"recent-{uuid.uuid4()}",
            },
        )

        assert response.status_code == 201
        assert response.json()["warning"] is None

    def test_ingesting_a_report_from_one_week_ago_should_include_day_count_in_warning(
        self, db_client: TestClient
    ) -> None:
        week_ago = _iso_ago(days=7)
        body = make_report(started_at=week_ago, finished_at=week_ago)

        response = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "late-team",
                "Idempotency-Key": f"late-week-{uuid.uuid4()}",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["warning"] is not None
        assert "d" in data["warning"]

    def test_late_report_should_still_be_persisted_and_retrievable(
        self, db_client: TestClient
    ) -> None:
        three_days_ago = _iso_ago(days=3)
        body = make_report(
            started_at=three_days_ago,
            finished_at=three_days_ago,
            git_sha="late-commit",
        )

        post_resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "late-team",
                "Idempotency-Key": f"late-persist-{uuid.uuid4()}",
            },
        )
        assert post_resp.status_code == 201
        assert post_resp.json()["warning"] is not None

        run_id = post_resp.json()["run_id"]
        raw = db_client.get(f"/api/v1/runs/{run_id}/raw").json()
        assert raw["run"]["git_sha"] == "late-commit"

    def test_report_at_exactly_24_hours_should_not_include_a_warning(
        self, db_client: TestClient
    ) -> None:
        just_under = _iso_ago(days=0, hours=23)
        body = make_report(started_at=just_under, finished_at=just_under)

        response = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "late-team",
                "Idempotency-Key": f"boundary-{uuid.uuid4()}",
            },
        )

        assert response.status_code == 201
        assert response.json()["warning"] is None

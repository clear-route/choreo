"""End-to-end behaviour tests: ingest a report, then GET it back.

These tests verify the full ingest->persist->retrieve cycle against real
TimescaleDB.  They cover both happy-path and error-path behaviours, and
assert that correct HTTP status codes are returned for each case.

Requires the Chronicle Docker Compose stack:

    docker compose -f docker/compose.chronicle.yaml up -d
    cd packages/chronicle && alembic upgrade head
    pytest packages/chronicle/tests/test_ingest_roundtrip.py -m chronicle_db

Follows the project test naming convention: behaviour-oriented names
using "should" / "should not" (CLAUDE.md §Test style).
"""

from __future__ import annotations

import uuid

import pytest
from conftest import skip_no_db
from factories import make_handle, make_report, make_scenario
from fastapi.testclient import TestClient

pytestmark = pytest.mark.chronicle_db


def _roundtrip_report(**overrides) -> dict:
    """Build a report with roundtrip-specific defaults."""
    defaults = {
        "started_at": "2026-04-19T10:00:00+00:00",
        "finished_at": "2026-04-19T10:00:01+00:00",
        "project_name": "roundtrip-test",
        "git_sha": "deadbeef",
        "scenarios": [
            make_scenario(
                name="roundtrip-scenario",
                correlation_id="corr-rt",
                duration_ms=8.0,
                handles=[
                    make_handle(
                        topic="orders.created",
                        outcome="pass",
                        latency_ms=5.2,
                        budget_ms=50.0,
                        matcher_description="contains_fields({'status': 'CREATED'})",
                    ),
                    make_handle(
                        topic="audit.logged",
                        outcome="slow",
                        latency_ms=25.0,
                        budget_ms=10.0,
                        matcher_description="field_equals('action', 'CREATE')",
                        expected={"action": "CREATE"},
                        actual={"action": "CREATE", "ts": "2026-04-19T10:00:00Z"},
                        attempts=1,
                        reason="matched but over budget",
                        failures=[
                            {
                                "kind": "mismatch",
                                "path": "action",
                                "expected": "CREATE",
                                "actual": "UPDATE",
                            },
                        ],
                        diagnosis={"kind": "over_budget", "latency_ms": 25.0, "budget_ms": 10.0},
                    ),
                ],
                timeline=[
                    {
                        "offset_ms": 1.0,
                        "wall_clock": "2026-04-19T10:00:00.001Z",
                        "topic": "orders.created",
                        "action": "published",
                        "detail": "",
                    },
                    {
                        "offset_ms": 5.2,
                        "wall_clock": "2026-04-19T10:00:00.005Z",
                        "topic": "orders.created",
                        "action": "matched",
                        "detail": "",
                    },
                    {
                        "offset_ms": 25.0,
                        "wall_clock": "2026-04-19T10:00:00.025Z",
                        "topic": "audit.logged",
                        "action": "matched",
                        "detail": "over budget",
                    },
                ],
                summary_text="scenario 'roundtrip-scenario' passed",
            ),
        ],
    }
    defaults.update(overrides)
    return make_report(**defaults)


# --- Happy path: ingest + retrieve ---


@skip_no_db
class TestIngestAndRetrieveRoundtrip:
    """POST a report, then GET it back and verify correctness."""

    def test_ingested_report_should_be_retrievable_via_get_raw(self, db_client: TestClient) -> None:
        body = _roundtrip_report()
        idem_key = f"roundtrip-raw-{uuid.uuid4()}"

        # Ingest
        post_resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "roundtrip-team",
                "Idempotency-Key": idem_key,
            },
        )
        assert post_resp.status_code == 201
        run_id = post_resp.json()["run_id"]

        # Retrieve raw
        get_resp = db_client.get(f"/api/v1/runs/{run_id}/raw")
        assert get_resp.status_code == 200

        raw = get_resp.json()
        assert raw["schema_version"] == "1"
        assert raw["run"]["transport"] == "MockTransport"
        assert raw["run"]["git_sha"] == "deadbeef"
        assert raw["run"]["totals"]["total"] == 1
        assert len(raw["tests"]) == 1
        assert raw["tests"][0]["scenarios"][0]["name"] == "roundtrip-scenario"

    def test_ingested_report_should_preserve_handle_data_in_raw(
        self, db_client: TestClient
    ) -> None:
        body = _roundtrip_report()
        idem_key = f"roundtrip-handles-{uuid.uuid4()}"

        post_resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "roundtrip-team",
                "Idempotency-Key": idem_key,
            },
        )
        run_id = post_resp.json()["run_id"]

        raw = db_client.get(f"/api/v1/runs/{run_id}/raw").json()

        handles = raw["tests"][0]["scenarios"][0]["handles"]
        assert len(handles) == 2
        assert handles[0]["topic"] == "orders.created"
        assert handles[0]["latency_ms"] == 5.2
        assert handles[1]["topic"] == "audit.logged"
        assert handles[1]["diagnosis"]["kind"] == "over_budget"
        assert handles[1]["failures"][0]["kind"] == "mismatch"

    def test_ingested_report_should_preserve_timeline_in_raw(self, db_client: TestClient) -> None:
        body = _roundtrip_report()
        idem_key = f"roundtrip-timeline-{uuid.uuid4()}"

        post_resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "roundtrip-team",
                "Idempotency-Key": idem_key,
            },
        )
        run_id = post_resp.json()["run_id"]

        raw = db_client.get(f"/api/v1/runs/{run_id}/raw").json()

        timeline = raw["tests"][0]["scenarios"][0]["timeline"]
        assert len(timeline) == 3
        assert timeline[0]["action"] == "published"
        assert timeline[2]["action"] == "matched"

    def test_ingested_run_should_be_retrievable_via_get_detail(self, db_client: TestClient) -> None:
        body = _roundtrip_report()
        idem_key = f"roundtrip-detail-{uuid.uuid4()}"

        post_resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "roundtrip-team",
                "Idempotency-Key": idem_key,
            },
        )
        run_id = post_resp.json()["run_id"]

        get_resp = db_client.get(f"/api/v1/runs/{run_id}")
        assert get_resp.status_code == 200

        detail = get_resp.json()
        assert detail["id"] == run_id
        assert detail["transport"] == "MockTransport"
        assert detail["environment"] == "test"
        assert detail["total_tests"] == 1
        assert detail["total_passed"] == 1
        assert len(detail["scenarios"]) == 1
        assert detail["scenarios"][0]["name"] == "roundtrip-scenario"
        assert detail["scenarios"][0]["outcome"] == "pass"

    def test_ingest_response_should_report_correct_counts(self, db_client: TestClient) -> None:
        body = _roundtrip_report()
        idem_key = f"roundtrip-counts-{uuid.uuid4()}"

        resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "roundtrip-team",
                "Idempotency-Key": idem_key,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["scenarios_ingested"] == 1
        assert data["handles_ingested"] == 2
        assert data["duplicate"] is False

    @pytest.mark.skipif(
        not (
            __import__("pathlib").Path(__file__).resolve().parents[2]
            / "core"
            / "test-report"
            / "results.json"
        ).exists(),
        reason="Real test report not available",
    )
    def test_real_core_report_should_roundtrip_correctly(
        self, db_client: TestClient, real_report: dict
    ) -> None:
        idem_key = f"roundtrip-real-{uuid.uuid4()}"

        post_resp = db_client.post(
            "/api/v1/runs",
            json=real_report,
            headers={
                "X-Chronicle-Tenant": "roundtrip-team",
                "Idempotency-Key": idem_key,
            },
        )
        assert post_resp.status_code == 201
        run_id = post_resp.json()["run_id"]

        raw = db_client.get(f"/api/v1/runs/{run_id}/raw").json()
        assert raw["run"]["totals"]["total"] == 426
        assert raw["run"]["totals"]["slow"] == 2
        assert len(raw["tests"]) == 426


# --- Error paths: ingestion ---


@skip_no_db
class TestIngestErrorCodes:
    """Verify correct HTTP status codes for malformed requests."""

    def test_missing_schema_version_should_return_422(self, db_client: TestClient) -> None:
        body = _roundtrip_report()
        del body["schema_version"]

        resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "error-team"},
        )
        assert resp.status_code == 422

    def test_unsupported_schema_version_should_return_422(self, db_client: TestClient) -> None:
        body = _roundtrip_report(schema_version="2")

        resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "error-team"},
        )
        assert resp.status_code == 422

    def test_missing_run_field_should_return_422(self, db_client: TestClient) -> None:
        body = _roundtrip_report()
        del body["run"]

        resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "error-team"},
        )
        assert resp.status_code == 422

    def test_missing_tests_field_should_return_422(self, db_client: TestClient) -> None:
        body = _roundtrip_report()
        del body["tests"]

        resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "error-team"},
        )
        assert resp.status_code == 422

    def test_missing_tenant_header_should_return_422(self, db_client: TestClient) -> None:
        body = _roundtrip_report()

        resp = db_client.post("/api/v1/runs", json=body)
        assert resp.status_code == 422

    def test_invalid_tenant_slug_should_return_422(self, db_client: TestClient) -> None:
        body = _roundtrip_report()

        resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "INVALID SLUG!!"},
        )
        assert resp.status_code == 422

    def test_single_character_tenant_slug_should_return_422(self, db_client: TestClient) -> None:
        body = _roundtrip_report()

        resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "x"},
        )
        assert resp.status_code == 422

    def test_empty_body_should_return_422(self, db_client: TestClient) -> None:
        resp = db_client.post(
            "/api/v1/runs",
            content=b"",
            headers={
                "X-Chronicle-Tenant": "error-team",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 422

    def test_non_json_body_should_return_422(self, db_client: TestClient) -> None:
        resp = db_client.post(
            "/api/v1/runs",
            content=b"this is not json",
            headers={
                "X-Chronicle-Tenant": "error-team",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 422

    def test_error_response_should_have_error_and_detail_fields(
        self, db_client: TestClient
    ) -> None:
        body = _roundtrip_report(schema_version="2")

        resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "error-team"},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "error" in data
        assert "detail" in data

    def test_error_response_should_not_leak_internal_details(self, db_client: TestClient) -> None:
        body = _roundtrip_report(schema_version="2")

        resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "error-team"},
        )
        text = resp.text
        assert "Traceback" not in text
        assert "File " not in text


# --- Error paths: retrieval ---


@skip_no_db
class TestRetrievalErrorCodes:
    """Verify correct HTTP status codes for GET requests."""

    def test_get_run_with_nonexistent_id_should_return_404(self, db_client: TestClient) -> None:
        fake_id = str(uuid.uuid4())
        resp = db_client.get(f"/api/v1/runs/{fake_id}")
        assert resp.status_code == 404

    def test_get_raw_with_nonexistent_id_should_return_404(self, db_client: TestClient) -> None:
        fake_id = str(uuid.uuid4())
        resp = db_client.get(f"/api/v1/runs/{fake_id}/raw")
        assert resp.status_code == 404

    def test_get_run_with_invalid_uuid_should_return_422(self, db_client: TestClient) -> None:
        resp = db_client.get("/api/v1/runs/not-a-uuid")
        assert resp.status_code == 422

    def test_get_raw_with_invalid_uuid_should_return_422(self, db_client: TestClient) -> None:
        resp = db_client.get("/api/v1/runs/not-a-uuid/raw")
        assert resp.status_code == 422


# --- Idempotency ---


@skip_no_db
class TestIdempotency:
    """Verify idempotency key behaviour."""

    def test_duplicate_idempotency_key_should_return_same_run_id(
        self, db_client: TestClient
    ) -> None:
        body = _roundtrip_report()
        idem_key = f"idem-dup-{uuid.uuid4()}"
        headers = {
            "X-Chronicle-Tenant": "idem-team",
            "Idempotency-Key": idem_key,
        }

        r1 = db_client.post("/api/v1/runs", json=body, headers=headers)
        r2 = db_client.post("/api/v1/runs", json=body, headers=headers)

        assert r1.status_code == 201
        assert r2.json()["duplicate"] is True
        assert r2.json()["run_id"] == r1.json()["run_id"]

    def test_different_idempotency_keys_should_create_separate_runs(
        self, db_client: TestClient
    ) -> None:
        body = _roundtrip_report()

        r1 = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "idem-team",
                "Idempotency-Key": f"idem-a-{uuid.uuid4()}",
            },
        )
        r2 = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "idem-team",
                "Idempotency-Key": f"idem-b-{uuid.uuid4()}",
            },
        )

        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["run_id"] != r2.json()["run_id"]

    def test_no_idempotency_key_should_always_create_new_run(self, db_client: TestClient) -> None:
        body = _roundtrip_report()
        headers = {"X-Chronicle-Tenant": "idem-team"}

        r1 = db_client.post("/api/v1/runs", json=body, headers=headers)
        r2 = db_client.post("/api/v1/runs", json=body, headers=headers)

        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["run_id"] != r2.json()["run_id"]


# --- Tenant normalisation ---


@skip_no_db
class TestTenantNormalisation:
    """Verify tenant slug is normalised to lowercase."""

    def test_uppercase_tenant_slug_should_be_normalised_and_retrievable(
        self, db_client: TestClient
    ) -> None:
        body = _roundtrip_report()
        idem_key = f"tenant-norm-{uuid.uuid4()}"

        resp = db_client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "Team-Alpha",
                "Idempotency-Key": idem_key,
            },
        )
        assert resp.status_code == 201
        run_id = resp.json()["run_id"]

        # Should be retrievable
        detail = db_client.get(f"/api/v1/runs/{run_id}")
        assert detail.status_code == 200

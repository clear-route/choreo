"""Integration tests for the ingest endpoint (POST /api/v1/runs).

Uses FastAPI's ``TestClient`` with mocked services to verify the route
handler, request validation, DI wiring, and response shapes without
requiring a running database.

Follows the project test naming convention: behaviour-oriented names
using "should" / "should not" (CLAUDE.md §Test style).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from chronicle.app import create_app
from chronicle.config import Settings
from chronicle.dependencies import get_ingest_service
from chronicle.schemas.ingest import IngestResponse
from chronicle.services.ingest_service import IngestService
from factories import make_report
from fastapi.testclient import TestClient

REPORT_PATH = Path(__file__).resolve().parents[2] / "core" / "test-report" / "results.json"


def _mock_ingest_service(
    *,
    run_id=None,
    handles: int = 0,
    scenarios: int = 0,
    duplicate: bool = False,
) -> IngestService:
    """Create a mock IngestService whose ``ingest()`` returns a canned response."""
    service = AsyncMock(spec=IngestService)
    service.ingest.return_value = IngestResponse(
        run_id=run_id or uuid4(),
        duplicate=duplicate,
        handles_ingested=handles,
        scenarios_ingested=scenarios,
    )
    return service


def _make_client(
    settings: Settings,
    service: IngestService | None = None,
) -> TestClient:
    """Build a TestClient with the ingest service overridden."""
    if service is None:
        service = _mock_ingest_service()

    app = create_app(settings=settings)
    app.dependency_overrides[get_ingest_service] = lambda: service

    return TestClient(app)


class TestIngestValidation:
    """Request validation for POST /api/v1/runs."""

    def test_ingesting_with_schema_version_2_should_return_422(self, settings: Settings) -> None:
        client = _make_client(settings)
        body = make_report(schema_version="2")

        response = client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "team-alpha"},
        )

        assert response.status_code == 422

    def test_ingesting_with_schema_version_1_1_should_be_accepted(self, settings: Settings) -> None:
        service = _mock_ingest_service(scenarios=0, handles=0)
        client = _make_client(settings, service)
        body = make_report(schema_version="1.1")

        response = client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "team-alpha"},
        )

        assert response.status_code == 201

    def test_ingesting_without_tenant_header_should_return_422(self, settings: Settings) -> None:
        client = _make_client(settings)
        body = make_report()

        response = client.post("/api/v1/runs", json=body)

        assert response.status_code == 422

    def test_ingesting_with_missing_schema_version_should_return_422(
        self, settings: Settings
    ) -> None:
        client = _make_client(settings)
        body = make_report()
        del body["schema_version"]

        response = client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "team-alpha"},
        )

        assert response.status_code == 422

    def test_ingesting_with_missing_run_field_should_return_422(self, settings: Settings) -> None:
        client = _make_client(settings)
        body = make_report()
        del body["run"]

        response = client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "team-alpha"},
        )

        assert response.status_code == 422

    def test_ingesting_with_invalid_tenant_slug_should_return_422(self, settings: Settings) -> None:
        client = _make_client(settings)
        body = make_report()

        response = client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "INVALID SLUG!!"},
        )

        assert response.status_code == 422


class TestIngestResponseShape:
    """Verify the response body shape for successful ingests."""

    def test_successful_ingest_should_return_201_with_run_id(self, settings: Settings) -> None:
        run_id = uuid4()
        service = _mock_ingest_service(run_id=run_id, scenarios=0, handles=0)
        client = _make_client(settings, service)
        body = make_report()

        response = client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "team-alpha"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["run_id"] == str(run_id)
        assert data["duplicate"] is False
        assert data["handles_ingested"] == 0
        assert data["scenarios_ingested"] == 0

    def test_ingest_response_should_include_scenario_and_handle_counts(
        self, settings: Settings
    ) -> None:
        service = _mock_ingest_service(scenarios=3, handles=12)
        client = _make_client(settings, service)
        body = make_report()

        response = client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "team-alpha"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["scenarios_ingested"] == 3
        assert data["handles_ingested"] == 12

    def test_tenant_slug_should_be_normalised_to_lowercase(self, settings: Settings) -> None:
        service = _mock_ingest_service()
        client = _make_client(settings, service)
        body = make_report()

        response = client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "Team-Alpha"},
        )

        assert response.status_code == 201
        # Verify the service was called with lowercase slug
        service.ingest.assert_called_once()
        call_args = service.ingest.call_args
        assert call_args[0][1] == "team-alpha"

    def test_idempotency_key_should_be_passed_to_service(self, settings: Settings) -> None:
        service = _mock_ingest_service()
        client = _make_client(settings, service)
        body = make_report()

        response = client.post(
            "/api/v1/runs",
            json=body,
            headers={
                "X-Chronicle-Tenant": "team-alpha",
                "Idempotency-Key": "my-key-123",
            },
        )

        assert response.status_code == 201
        service.ingest.assert_called_once()
        call_args = service.ingest.call_args
        assert call_args[0][2] == "my-key-123"

    def test_validation_errors_should_not_include_input_values(self, settings: Settings) -> None:
        """Verify that Pydantic error responses strip input values (security)."""
        client = _make_client(settings)
        body = {"schema_version": "2", "run": {}, "tests": []}

        response = client.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "team-alpha"},
        )

        assert response.status_code == 422
        body_text = response.text
        assert "'input'" not in body_text


class TestIngestWithRealReport:
    """Verify the endpoint accepts the real core test report."""

    @pytest.mark.skipif(
        not REPORT_PATH.exists(),
        reason="Real test report not available",
    )
    def test_ingesting_the_real_core_test_report_should_return_201(
        self, settings: Settings
    ) -> None:
        real_report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        service = _mock_ingest_service(scenarios=100, handles=200)
        client = _make_client(settings, service)

        response = client.post(
            "/api/v1/runs",
            json=real_report,
            headers={"X-Chronicle-Tenant": "core-team"},
        )

        assert response.status_code == 201
        service.ingest.assert_called_once()
        # Verify the IngestRequest was constructed correctly
        request_arg = service.ingest.call_args[0][0]
        assert request_arg.schema_version == "1"
        assert request_arg.run["totals"]["total"] == 426

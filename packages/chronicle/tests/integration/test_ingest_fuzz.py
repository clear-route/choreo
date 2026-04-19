"""Fuzz testing for the ingest and retrieval endpoints.

Uses Hypothesis to generate a wide variety of malformed, edge-case, and
valid payloads, verifying that the API never returns 500 and always
responds with the correct status code category.

This catches unhandled exceptions, crashes on unexpected input shapes,
and validation gaps that hand-crafted tests miss.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from chronicle.app import create_app
from chronicle.config import Settings
from chronicle.dependencies import get_ingest_service, get_session
from chronicle.schemas.ingest import IngestResponse
from chronicle.services.ingest_service import IngestService
from factories import make_report
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession


def _mock_service() -> IngestService:
    service = AsyncMock(spec=IngestService)
    service.ingest.return_value = IngestResponse(
        run_id=uuid4(),
        duplicate=False,
        handles_ingested=0,
        scenarios_ingested=0,
    )
    return service


def _fuzz_client() -> TestClient:
    """Build a TestClient with all DB dependencies mocked.

    Both the ingest service and the session are overridden so the
    GET endpoints (which use ``get_session``) also work without a DB.
    """
    app_settings = Settings(database_url="postgresql+asyncpg://test:test@localhost:5432/test")
    app = create_app(settings=app_settings)
    app.dependency_overrides[get_ingest_service] = _mock_service

    # Mock session for GET endpoints (health, runs/{id}, runs/{id}/raw)
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute.return_value = MagicMock(
        scalar_one_or_none=MagicMock(return_value=None),
        scalar_one=MagicMock(return_value=1),
    )
    app.dependency_overrides[get_session] = lambda: mock_session

    return TestClient(app)


_CLIENT = _fuzz_client()

# Tenant slugs must be ASCII-safe for HTTP headers.
_ascii_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
        max_codepoint=127,
    ),
    max_size=200,
)

# UUID-like strings (no slashes, which create different URL paths)
_uuid_like = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), max_codepoint=127),
    min_size=1,
    max_size=100,
)


class TestIngestShouldNeverReturn500:
    """The ingest endpoint should return 4xx for bad input, never 5xx."""

    @given(
        body=st.dictionaries(
            keys=st.text(max_size=20),
            values=st.one_of(
                st.none(),
                st.booleans(),
                st.integers(),
                st.text(max_size=100),
                st.lists(st.none(), max_size=3),
            ),
            max_size=10,
        )
    )
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=200)
    def test_random_json_bodies_should_not_cause_500(self, body: dict) -> None:
        response = _CLIENT.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "fuzz-team"},
        )
        assert response.status_code < 500

    @given(tenant=_ascii_text)
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=100)
    def test_random_tenant_slugs_should_not_cause_500(self, tenant: str) -> None:
        assume(len(tenant) > 0)  # empty header causes a different error path
        body = make_report()
        response = _CLIENT.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": tenant},
        )
        assert response.status_code < 500

    @given(version=st.text(max_size=50))
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=100)
    def test_random_schema_versions_should_not_cause_500(self, version: str) -> None:
        body = make_report()
        body["schema_version"] = version
        response = _CLIENT.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "fuzz-team"},
        )
        assert response.status_code < 500

    @given(
        content=st.one_of(
            st.binary(max_size=1000),
            st.text(max_size=1000).map(lambda t: t.encode("utf-8", errors="replace")),
        )
    )
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=100)
    def test_random_binary_bodies_should_not_cause_500(self, content: bytes) -> None:
        response = _CLIENT.post(
            "/api/v1/runs",
            content=content,
            headers={
                "X-Chronicle-Tenant": "fuzz-team",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code < 500

    @given(
        run=st.dictionaries(
            keys=st.text(max_size=20),
            values=st.one_of(st.none(), st.text(max_size=50), st.integers()),
            max_size=5,
        )
    )
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=100)
    def test_random_run_dicts_should_not_cause_500(self, run: dict) -> None:
        body = {"schema_version": "1", "run": run, "tests": []}
        response = _CLIENT.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "fuzz-team"},
        )
        assert response.status_code < 500


class TestGetEndpointsShouldNeverReturn500:
    """GET endpoints should handle invalid UUIDs gracefully."""

    @given(uuid_str=_uuid_like)
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=100)
    def test_random_run_ids_on_get_should_not_cause_500(self, uuid_str: str) -> None:
        response = _CLIENT.get(f"/api/v1/runs/{uuid_str}")
        assert response.status_code < 500

    @given(uuid_str=_uuid_like)
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=100)
    def test_random_run_ids_on_get_raw_should_not_cause_500(self, uuid_str: str) -> None:
        response = _CLIENT.get(f"/api/v1/runs/{uuid_str}/raw")
        assert response.status_code < 500


class TestIngestValidReportsShouldSucceed:
    """Valid reports generated by Hypothesis should always return 201."""

    @given(
        environment=st.one_of(st.none(), st.text(min_size=1, max_size=50)),
        branch=st.one_of(st.none(), st.text(min_size=1, max_size=50)),
        transport=st.text(min_size=1, max_size=50),
        n_handles=st.integers(min_value=0, max_value=5),
    )
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=100)
    def test_valid_reports_with_varied_metadata_should_return_201(
        self, environment, branch, transport, n_handles
    ) -> None:
        from factories import make_handle

        handles = [make_handle(topic=f"topic.{i}") for i in range(n_handles)]
        body = make_report(
            environment=environment,
            branch=branch,
            transport=transport,
            handles=handles if handles else None,
        )
        response = _CLIENT.post(
            "/api/v1/runs",
            json=body,
            headers={"X-Chronicle-Tenant": "fuzz-team"},
        )
        assert response.status_code == 201

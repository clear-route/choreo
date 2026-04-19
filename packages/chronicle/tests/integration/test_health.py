"""Integration tests for the health check endpoint.

These tests use FastAPI's ``TestClient`` with a mocked database session
to verify the health endpoint's behaviour without requiring a running
TimescaleDB instance.  The session mock is injected via FastAPI's
dependency override mechanism (see ``conftest.py``).

Follows the project test naming convention: behaviour-oriented names
using "should" / "should not" (CLAUDE.md §Test style).
"""

from unittest.mock import AsyncMock, MagicMock

from chronicle.app import create_app
from chronicle.config import Settings
from chronicle.dependencies import get_session
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession


def _make_session(*execute_results: object) -> AsyncMock:
    """Build a mock AsyncSession whose ``execute`` returns the given results.

    Each result is wrapped in a ``MagicMock`` so that ``scalar_one()`` is a
    regular (non-async) call -- matching SQLAlchemy's ``CursorResult`` API
    where ``scalar_one`` is synchronous on the result object returned by
    the *awaited* ``session.execute()``.
    """
    session = AsyncMock(spec=AsyncSession)
    results = []
    for value in execute_results:
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = value
        results.append(mock_result)
    if len(results) == 1:
        session.execute.return_value = results[0]
    else:
        session.execute.side_effect = results
    return session


class TestShallowHealthCheck:
    """GET /api/v1/health (default, no ``?deep`` parameter)."""

    def test_health_should_return_ok_when_database_is_connected(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "connected"

    def test_health_should_return_503_when_database_is_unreachable(
        self, settings: Settings
    ) -> None:
        failing_session = AsyncMock(spec=AsyncSession)
        failing_session.execute.side_effect = ConnectionRefusedError("could not connect to server")

        app = create_app(settings=settings)
        app.dependency_overrides[get_session] = lambda: failing_session
        client = TestClient(app)

        response = client.get("/api/v1/health")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["database"] == "unreachable"
        # error_type is not exposed -- no internal details in responses
        assert "error_type" not in body

    def test_health_should_not_include_hypertable_field_by_default(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        assert "hypertable" not in response.json()


class TestDeepHealthCheck:
    """GET /api/v1/health?deep=true -- additionally checks for the hypertable."""

    def test_deep_health_should_return_ok_when_hypertable_exists(self, settings: Settings) -> None:
        # First execute: SELECT 1 (shallow), second: hypertable count = 1
        session = _make_session(1, 1)

        app = create_app(settings=settings)
        app.dependency_overrides[get_session] = lambda: session
        client = TestClient(app)

        response = client.get("/api/v1/health", params={"deep": "true"})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "connected"
        assert body["hypertable"] == "present"

    def test_deep_health_should_return_503_when_hypertable_is_missing(
        self, settings: Settings
    ) -> None:
        # First execute: SELECT 1 (shallow), second: hypertable count = 0
        session = _make_session(1, 0)

        app = create_app(settings=settings)
        app.dependency_overrides[get_session] = lambda: session
        client = TestClient(app)

        response = client.get("/api/v1/health", params={"deep": "true"})

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["hypertable"] == "missing"

    def test_deep_health_should_return_503_when_timescaledb_query_fails(
        self, settings: Settings
    ) -> None:
        session = AsyncMock(spec=AsyncSession)
        shallow_result = MagicMock()
        # Shallow succeeds, deep query fails (e.g. extension not loaded)
        session.execute.side_effect = [
            shallow_result,
            Exception("relation 'timescaledb_information.hypertables' does not exist"),
        ]

        app = create_app(settings=settings)
        app.dependency_overrides[get_session] = lambda: session
        client = TestClient(app)

        response = client.get("/api/v1/health", params={"deep": "true"})

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["hypertable"] == "check_failed"


class TestErrorResponseShape:
    """Verify that error responses follow the ErrorResponse schema."""

    def test_unhandled_exception_should_return_consistent_error_shape(
        self, settings: Settings
    ) -> None:
        session = AsyncMock(spec=AsyncSession)
        session.execute.side_effect = RuntimeError("unexpected")

        app = create_app(settings=settings)
        app.dependency_overrides[get_session] = lambda: session
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/api/v1/health")

        # The health endpoint catches exceptions itself and returns 503.
        assert response.status_code == 503
        body = response.json()
        assert "status" in body
        assert "database" in body
        # No internal details leaked
        assert "error_type" not in body

    def test_error_responses_should_not_include_stack_traces(self, settings: Settings) -> None:
        session = AsyncMock(spec=AsyncSession)
        session.execute.side_effect = RuntimeError("internal details here")

        app = create_app(settings=settings)
        app.dependency_overrides[get_session] = lambda: session
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/api/v1/health")
        body_text = response.text

        assert "Traceback" not in body_text
        assert "internal details here" not in body_text

    def test_404_should_return_consistent_error_shape(self, client: TestClient) -> None:
        response = client.get("/api/v1/nonexistent")

        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert "detail" in body

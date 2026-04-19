"""Integration tests for the tenants endpoint (GET /api/v1/tenants).

Uses FastAPI's ``TestClient`` with mocked repositories to verify the route
handler, pagination, response shapes, and validation without requiring a
running database.

Follows the project test naming convention: behaviour-oriented names
using "should" / "should not" (CLAUDE.md §Test style).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from chronicle.app import create_app
from chronicle.config import Settings
from chronicle.dependencies import get_run_repo
from chronicle.repositories.run_repo import RunRepository
from fastapi.testclient import TestClient


def _make_tenant_row(
    *,
    slug: str = "team-alpha",
    name: str = "team-alpha",
    run_count: int = 0,
):
    """Build a mock tenant row matching the list_tenants query shape."""

    class _Row:
        def __init__(self, id, slug, name, created_at, run_count):
            self.id = id
            self.slug = slug
            self.name = name
            self.created_at = created_at
            self.run_count = run_count

    return _Row(
        id=uuid4(),
        slug=slug,
        name=name,
        created_at=datetime(2026, 4, 19, tzinfo=UTC),
        run_count=run_count,
    )


def _mock_run_repo(
    *,
    tenants: list | None = None,
    total: int | None = None,
) -> RunRepository:
    """Create a mock RunRepository whose ``list_tenants()`` returns canned data."""
    repo = AsyncMock(spec=RunRepository)
    tenant_list = tenants if tenants is not None else []
    repo.list_tenants.return_value = (
        tenant_list,
        total if total is not None else len(tenant_list),
    )
    return repo


def _make_client(settings: Settings, repo: RunRepository | None = None) -> TestClient:
    """Build a TestClient with the run repo overridden."""
    if repo is None:
        repo = _mock_run_repo()

    app = create_app(settings=settings)
    app.dependency_overrides[get_run_repo] = lambda: repo

    return TestClient(app)


class TestTenantsListResponseShape:
    """Verify the response body shape for GET /api/v1/tenants."""

    def test_listing_tenants_should_return_paginated_envelope(self, settings: Settings) -> None:
        tenants = [
            _make_tenant_row(slug="team-alpha", run_count=5),
            _make_tenant_row(slug="team-beta", run_count=12),
        ]
        repo = _mock_run_repo(tenants=tenants, total=2)
        client = _make_client(settings, repo)

        response = client.get("/api/v1/tenants")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["limit"] == 50
        assert data["offset"] == 0
        assert len(data["items"]) == 2

    def test_each_tenant_should_include_required_fields(self, settings: Settings) -> None:
        tenant = _make_tenant_row(slug="platform-eng", name="platform-eng", run_count=3)
        repo = _mock_run_repo(tenants=[tenant])
        client = _make_client(settings, repo)

        response = client.get("/api/v1/tenants")

        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["slug"] == "platform-eng"
        assert item["name"] == "platform-eng"
        assert item["run_count"] == 3
        assert "id" in item
        assert "created_at" in item

    def test_listing_tenants_with_no_tenants_should_return_empty_list(
        self, settings: Settings
    ) -> None:
        repo = _mock_run_repo(tenants=[], total=0)
        client = _make_client(settings, repo)

        response = client.get("/api/v1/tenants")

        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_tenant_run_count_should_reflect_the_number_of_ingested_runs(
        self, settings: Settings
    ) -> None:
        tenants = [
            _make_tenant_row(slug="active-team", run_count=42),
            _make_tenant_row(slug="new-team", run_count=0),
        ]
        repo = _mock_run_repo(tenants=tenants)
        client = _make_client(settings, repo)

        response = client.get("/api/v1/tenants")

        items = response.json()["items"]
        by_slug = {i["slug"]: i for i in items}
        assert by_slug["active-team"]["run_count"] == 42
        assert by_slug["new-team"]["run_count"] == 0


class TestTenantsPagination:
    """Verify pagination parameters for GET /api/v1/tenants."""

    def test_listing_tenants_should_pass_limit_and_offset_to_repository(
        self, settings: Settings
    ) -> None:
        repo = _mock_run_repo()
        client = _make_client(settings, repo)

        client.get("/api/v1/tenants?limit=10&offset=20")

        repo.list_tenants.assert_called_once_with(limit=10, offset=20)

    def test_listing_tenants_without_pagination_params_should_use_defaults(
        self, settings: Settings
    ) -> None:
        repo = _mock_run_repo()
        client = _make_client(settings, repo)

        client.get("/api/v1/tenants")

        repo.list_tenants.assert_called_once_with(limit=50, offset=0)

    def test_listing_tenants_should_reflect_pagination_params_in_response(
        self, settings: Settings
    ) -> None:
        repo = _mock_run_repo(tenants=[], total=100)
        client = _make_client(settings, repo)

        response = client.get("/api/v1/tenants?limit=10&offset=20")

        data = response.json()
        assert data["limit"] == 10
        assert data["offset"] == 20
        assert data["total"] == 100


class TestTenantsValidation:
    """Verify input validation for GET /api/v1/tenants query parameters."""

    def test_listing_tenants_with_limit_below_1_should_return_422(self, settings: Settings) -> None:
        client = _make_client(settings)

        response = client.get("/api/v1/tenants?limit=0")

        assert response.status_code == 422

    def test_listing_tenants_with_limit_above_500_should_return_422(
        self, settings: Settings
    ) -> None:
        client = _make_client(settings)

        response = client.get("/api/v1/tenants?limit=501")

        assert response.status_code == 422

    def test_listing_tenants_with_negative_offset_should_return_422(
        self, settings: Settings
    ) -> None:
        client = _make_client(settings)

        response = client.get("/api/v1/tenants?offset=-1")

        assert response.status_code == 422

    def test_listing_tenants_with_non_integer_limit_should_return_422(
        self, settings: Settings
    ) -> None:
        client = _make_client(settings)

        response = client.get("/api/v1/tenants?limit=abc")

        assert response.status_code == 422

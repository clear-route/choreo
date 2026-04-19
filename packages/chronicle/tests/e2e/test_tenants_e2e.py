"""End-to-end tests for the tenants endpoint against real TimescaleDB.

Ingest reports for different tenants, then verify the GET /api/v1/tenants
endpoint returns them with correct run counts and pagination.

Requires the Chronicle Docker Compose stack:

    docker compose -f docker/compose.chronicle.yaml up -d
    cd packages/chronicle && alembic upgrade head
    pytest packages/chronicle/tests/e2e/test_tenants_e2e.py -m chronicle_db

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


def _tenant_report(**overrides) -> dict:
    """Build a minimal report for tenant e2e tests."""
    defaults = {
        "started_at": "2026-04-19T10:00:00+00:00",
        "finished_at": "2026-04-19T10:00:01+00:00",
        "project_name": "tenant-e2e",
        "scenarios": [
            make_scenario(
                name="tenant-test-scenario",
                handles=[make_handle(topic="orders.created")],
            ),
        ],
    }
    defaults.update(overrides)
    return make_report(**defaults)


def _ingest(client: TestClient, tenant: str) -> str:
    """Post a report for a tenant and return the run_id."""
    resp = client.post(
        "/api/v1/runs",
        json=_tenant_report(),
        headers={
            "X-Chronicle-Tenant": tenant,
            "Idempotency-Key": f"tenant-e2e-{uuid.uuid4()}",
        },
    )
    assert resp.status_code == 201
    return resp.json()["run_id"]


@skip_no_db
class TestTenantsEndToEnd:
    """POST reports for tenants, then GET /api/v1/tenants to verify."""

    def test_ingested_tenant_should_appear_in_tenant_list(self, db_client: TestClient) -> None:
        _ingest(db_client, "tenant-e2e-alpha")

        resp = db_client.get("/api/v1/tenants")

        assert resp.status_code == 200
        data = resp.json()
        slugs = [t["slug"] for t in data["items"]]
        assert "tenant-e2e-alpha" in slugs

    def test_tenant_run_count_should_match_number_of_ingested_reports(
        self, db_client: TestClient
    ) -> None:
        _ingest(db_client, "tenant-e2e-counter")
        _ingest(db_client, "tenant-e2e-counter")
        _ingest(db_client, "tenant-e2e-counter")

        resp = db_client.get("/api/v1/tenants")

        assert resp.status_code == 200
        by_slug = {t["slug"]: t for t in resp.json()["items"]}
        assert by_slug["tenant-e2e-counter"]["run_count"] == 3

    def test_multiple_tenants_should_each_appear_with_independent_counts(
        self, db_client: TestClient
    ) -> None:
        _ingest(db_client, "tenant-e2e-one")
        _ingest(db_client, "tenant-e2e-one")
        _ingest(db_client, "tenant-e2e-two")

        resp = db_client.get("/api/v1/tenants")

        assert resp.status_code == 200
        by_slug = {t["slug"]: t for t in resp.json()["items"]}
        assert by_slug["tenant-e2e-one"]["run_count"] == 2
        assert by_slug["tenant-e2e-two"]["run_count"] == 1

    def test_tenant_list_should_return_paginated_envelope(self, db_client: TestClient) -> None:
        _ingest(db_client, "tenant-e2e-page")

        resp = db_client.get("/api/v1/tenants?limit=10&offset=0")

        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["limit"] == 10
        assert data["offset"] == 0
        assert data["total"] >= 1

    def test_tenant_list_should_respect_limit(self, db_client: TestClient) -> None:
        # Ensure at least 2 tenants exist
        _ingest(db_client, "tenant-e2e-lim1")
        _ingest(db_client, "tenant-e2e-lim2")

        resp = db_client.get("/api/v1/tenants?limit=1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["total"] >= 2

    def test_each_tenant_should_include_all_required_fields(self, db_client: TestClient) -> None:
        _ingest(db_client, "tenant-e2e-fields")

        resp = db_client.get("/api/v1/tenants")

        assert resp.status_code == 200
        tenant = next(t for t in resp.json()["items"] if t["slug"] == "tenant-e2e-fields")
        assert "id" in tenant
        assert "slug" in tenant
        assert "name" in tenant
        assert "created_at" in tenant
        assert "run_count" in tenant
        assert isinstance(tenant["run_count"], int)

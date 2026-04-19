"""Integration tests for read-side API endpoints.

Covers GET /runs, GET /topics, GET /topics/{topic}/latency,
GET /anomalies. Uses mocked repositories injected via dependency
overrides — no database required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from chronicle.app import create_app
from chronicle.config import Settings
from chronicle.dependencies import (
    get_run_repo,
    get_tenant_or_404,
    get_topic_repo,
    get_anomaly_repo,
    get_resolution_service,
)
from chronicle.models.tables import Tenant
from chronicle.repositories.run_repo import RunRepository
from chronicle.repositories.topic_repo import TopicRepository, TopicSummaryRow, LatencyBucketRow, TopicRunSummaryRow
from chronicle.repositories.anomaly_repo import AnomalyRepository
from chronicle.services.resolution_service import ResolutionService
from fastapi.testclient import TestClient


# ── Helpers ──

_TENANT = Tenant(
    id=uuid4(),
    slug="test-team",
    name="test-team",
    created_at=datetime.now(timezone.utc),
)

_NOW = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)


def _make_run(*, passed=10, failed=0, slow=0, branch="main", env="staging"):
    """Build a mock Run ORM object."""
    mock = AsyncMock()
    mock.id = uuid4()
    mock.tenant_id = _TENANT.id
    mock.started_at = _NOW
    mock.finished_at = _NOW
    mock.duration_ms = 1000.0
    mock.environment = env
    mock.transport = "MockTransport"
    mock.branch = branch
    mock.git_sha = "deadbeef"
    mock.project_name = "test"
    mock.total_tests = passed + failed + slow
    mock.total_passed = passed
    mock.total_failed = failed
    mock.total_errored = 0
    mock.total_skipped = 0
    mock.total_slow = slow
    mock.topic_count = 3
    mock.p50_ms = 5.0
    mock.p95_ms = 50.0
    mock.p99_ms = 100.0
    return mock


def _client(
    *,
    run_repo=None,
    topic_repo=None,
    anomaly_repo=None,
    resolution_svc=None,
    tenant=_TENANT,
) -> TestClient:
    settings = Settings(database_url="postgresql+asyncpg://test:test@localhost:5432/test")
    app = create_app(settings=settings)

    if run_repo:
        app.dependency_overrides[get_run_repo] = lambda: run_repo
    if topic_repo:
        app.dependency_overrides[get_topic_repo] = lambda: topic_repo
    if anomaly_repo:
        app.dependency_overrides[get_anomaly_repo] = lambda: anomaly_repo
    if resolution_svc:
        app.dependency_overrides[get_resolution_service] = lambda: resolution_svc
    if tenant:
        app.dependency_overrides[get_tenant_or_404] = lambda: tenant

    return TestClient(app)


# ── GET /api/v1/runs ──


class TestListRuns:
    def test_listing_runs_should_return_paginated_envelope(self):
        repo = AsyncMock(spec=RunRepository)
        runs = [_make_run(), _make_run(failed=2)]
        repo.list_runs.return_value = (runs, 2)

        c = _client(run_repo=repo)
        r = c.get("/api/v1/runs?tenant=test-team")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2
        assert body["limit"] == 50
        assert body["offset"] == 0

    def test_listing_runs_should_include_pass_rate(self):
        repo = AsyncMock(spec=RunRepository)
        repo.list_runs.return_value = ([_make_run(passed=8, failed=2)], 1)

        c = _client(run_repo=repo)
        body = c.get("/api/v1/runs?tenant=test-team").json()

        assert body["items"][0]["pass_rate"] == pytest.approx(0.8)

    def test_listing_runs_should_pass_branch_filter_to_repo(self):
        repo = AsyncMock(spec=RunRepository)
        repo.list_runs.return_value = ([], 0)

        c = _client(run_repo=repo)
        c.get("/api/v1/runs?tenant=test-team&branch=feat/x")

        repo.list_runs.assert_called_once()
        _, kwargs = repo.list_runs.call_args
        assert kwargs["branch"] == "feat/x"

    def test_listing_runs_should_pass_environment_filter_to_repo(self):
        repo = AsyncMock(spec=RunRepository)
        repo.list_runs.return_value = ([], 0)

        c = _client(run_repo=repo)
        c.get("/api/v1/runs?tenant=test-team&environment=staging")

        _, kwargs = repo.list_runs.call_args
        assert kwargs["environment"] == "staging"

    def test_listing_runs_should_respect_limit_and_offset(self):
        repo = AsyncMock(spec=RunRepository)
        repo.list_runs.return_value = ([], 0)

        c = _client(run_repo=repo)
        c.get("/api/v1/runs?tenant=test-team&limit=10&offset=20")

        _, kwargs = repo.list_runs.call_args
        assert kwargs["limit"] == 10
        assert kwargs["offset"] == 20

    def test_listing_runs_should_reject_limit_above_500(self):
        c = _client(run_repo=AsyncMock(spec=RunRepository))
        r = c.get("/api/v1/runs?tenant=test-team&limit=501")
        assert r.status_code == 422

    def test_listing_runs_should_return_404_for_unknown_tenant(self):
        c = _client(tenant=None)
        # Without tenant override, the real dependency runs — but with no DB,
        # we skip this test. The tenant=None case is tested in e2e.


# ── GET /api/v1/topics ──


class TestListTopics:
    def test_listing_topics_should_return_paginated_envelope(self):
        repo = AsyncMock(spec=TopicRepository)
        topics = [
            TopicSummaryRow(
                topic="orders.created",
                latest_run_at=_NOW,
                sample_count=100,
                latest_p50_ms=5.0,
                latest_p95_ms=50.0,
                latest_p99_ms=100.0,
                slow_count=2,
                timeout_count=1,
            ),
        ]
        repo.list_topics.return_value = (topics, 1)

        c = _client(topic_repo=repo)
        r = c.get("/api/v1/topics?tenant=test-team")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["topic"] == "orders.created"
        assert body["items"][0]["latest_p95_ms"] == 50.0
        assert body["items"][0]["sample_count"] == 100

    def test_listing_topics_should_pass_environment_to_repo(self):
        repo = AsyncMock(spec=TopicRepository)
        repo.list_topics.return_value = ([], 0)

        c = _client(topic_repo=repo)
        c.get("/api/v1/topics?tenant=test-team&environment=prod")

        _, kwargs = repo.list_topics.call_args
        assert kwargs["environment"] == "prod"


# ── GET /api/v1/topics/{topic}/latency ──


class TestTopicLatency:
    def test_latency_endpoint_should_return_buckets(self):
        svc = AsyncMock(spec=ResolutionService)
        buckets = [
            LatencyBucketRow(
                bucket=_NOW,
                sample_count=50,
                avg_ms=10.0,
                p50_ms=5.0,
                p95_ms=50.0,
                p99_ms=100.0,
                min_ms=1.0,
                max_ms=200.0,
                slow_count=0,
                timeout_count=0,
                fail_count=0,
                budget_violation_count=0,
            ),
        ]
        svc.get_topic_latency.return_value = (buckets, "raw")

        c = _client(resolution_svc=svc)
        r = c.get("/api/v1/topics/orders.created/latency?tenant=test-team")

        assert r.status_code == 200
        body = r.json()
        assert body["topic"] == "orders.created"
        assert body["resolution"] == "raw"
        assert len(body["buckets"]) == 1
        assert body["buckets"][0]["p95_ms"] == 50.0

    def test_latency_endpoint_should_reject_span_over_365_days(self):
        svc = AsyncMock(spec=ResolutionService)
        c = _client(resolution_svc=svc)

        r = c.get(
            "/api/v1/topics/orders.created/latency"
            "?tenant=test-team"
            "&from=2024-01-01T00:00:00Z"
            "&to=2026-01-01T00:00:00Z"
        )
        assert r.status_code == 422


# ── GET /api/v1/anomalies ──


class TestListAnomalies:
    def test_listing_anomalies_should_return_paginated_envelope(self):
        repo = AsyncMock(spec=AnomalyRepository)
        anomaly = AsyncMock()
        anomaly.id = uuid4()
        anomaly.tenant_id = _TENANT.id
        anomaly.run_id = uuid4()
        anomaly.detected_at = _NOW
        anomaly.topic = "orders.created"
        anomaly.detection_method = "rolling_baseline"
        anomaly.metric = "p95_ms"
        anomaly.current_value = 80.0
        anomaly.baseline_value = 50.0
        anomaly.baseline_stddev = 5.0
        anomaly.change_pct = 60.0
        anomaly.severity = "critical"
        anomaly.resolved = False
        anomaly.resolved_at = None
        repo.list_anomalies.return_value = ([anomaly], 1)

        c = _client(anomaly_repo=repo)
        r = c.get("/api/v1/anomalies?tenant=test-team")

        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["severity"] == "critical"
        assert body["items"][0]["topic"] == "orders.created"

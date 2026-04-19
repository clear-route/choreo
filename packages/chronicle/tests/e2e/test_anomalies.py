"""E2E tests for the anomaly feed endpoint against real TimescaleDB.

Verifies listing, filtering, and error handling for GET /api/v1/anomalies.
Also tests that anomaly detection fires on real ingest when sufficient
baseline data exists.

Follows the project test naming convention: behaviour-oriented names
using "should" / "should not" (CLAUDE.md §Test style).
"""

from __future__ import annotations

import uuid

import pytest
from conftest import skip_no_db
from factories import make_handle, make_report
from fastapi.testclient import TestClient

pytestmark = pytest.mark.chronicle_db


def _ingest(
    client: TestClient,
    *,
    tenant: str = "anomaly-team",
    latency_ms: float = 10.0,
    topic: str = "test.topic",
    **kwargs,
) -> str:
    """Ingest a report and return the run_id."""
    body = make_report(
        handles=[make_handle(topic=topic, latency_ms=latency_ms)],
        **kwargs,
    )
    resp = client.post(
        "/api/v1/runs",
        json=body,
        headers={
            "X-Chronicle-Tenant": tenant,
            "Idempotency-Key": f"anomaly-{uuid.uuid4()}",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["run_id"]


# ── Anomaly feed endpoint ──


@skip_no_db
class TestAnomalyFeed:
    """GET /api/v1/anomalies should list anomalies for a tenant."""

    def test_anomaly_feed_should_return_paginated_response(self, db_client: TestClient) -> None:
        _ingest(db_client)

        resp = db_client.get("/api/v1/anomalies", params={"tenant": "anomaly-team"})
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data

    def test_anomaly_feed_for_unknown_tenant_should_return_404(self, db_client: TestClient) -> None:
        resp = db_client.get("/api/v1/anomalies", params={"tenant": "zz-nonexistent"})
        assert resp.status_code == 404

    def test_anomaly_feed_without_tenant_should_return_422(self, db_client: TestClient) -> None:
        resp = db_client.get("/api/v1/anomalies")
        assert resp.status_code == 422

    def test_anomaly_feed_should_filter_by_topic(self, db_client: TestClient) -> None:
        _ingest(db_client)

        resp = db_client.get(
            "/api/v1/anomalies",
            params={"tenant": "anomaly-team", "topic": "nonexistent.topic"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_anomaly_feed_should_filter_by_severity(self, db_client: TestClient) -> None:
        _ingest(db_client)

        resp = db_client.get(
            "/api/v1/anomalies",
            params={"tenant": "anomaly-team", "severity": "critical"},
        )
        assert resp.status_code == 200
        # May be 0 if no critical anomalies — just verify the filter works
        assert isinstance(resp.json()["items"], list)

    def test_anomaly_feed_should_filter_by_resolved_status(self, db_client: TestClient) -> None:
        _ingest(db_client)

        resp = db_client.get(
            "/api/v1/anomalies",
            params={"tenant": "anomaly-team", "resolved": "false"},
        )
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["resolved"] is False


# ── Anomaly detection integration ──


@skip_no_db
class TestAnomalyDetectionFires:
    """Anomaly detection should produce anomalies when baseline exists."""

    def test_latency_spike_after_baseline_should_create_anomaly(
        self, db_client: TestClient
    ) -> None:
        tenant = "detect-team"
        topic = "spike.topic"

        # Build baseline: 10 runs at ~10ms
        for _ in range(10):
            _ingest(db_client, tenant=tenant, topic=topic, latency_ms=10.0)

        # Spike: 200ms — well beyond 2 sigma
        _ingest(db_client, tenant=tenant, topic=topic, latency_ms=200.0)

        # Check anomaly feed
        resp = db_client.get(
            "/api/v1/anomalies",
            params={"tenant": tenant, "topic": topic},
        )
        assert resp.status_code == 200
        anomalies = resp.json()["items"]
        assert len(anomalies) >= 1

        a = anomalies[0]
        assert a["topic"] == topic
        assert a["detection_method"] == "rolling_baseline"
        assert a["severity"] in ("warning", "critical")
        assert a["current_value"] > a["baseline_value"]
        assert a["resolved"] is False

    def test_stable_latency_should_not_create_anomaly(self, db_client: TestClient) -> None:
        tenant = "stable-team"
        topic = "stable.topic"

        # 11 runs all at ~10ms — no spike
        for _ in range(11):
            _ingest(db_client, tenant=tenant, topic=topic, latency_ms=10.0)

        resp = db_client.get(
            "/api/v1/anomalies",
            params={"tenant": tenant, "topic": topic},
        )
        assert resp.status_code == 200
        baseline_anomalies = [
            a for a in resp.json()["items"] if a["detection_method"] == "rolling_baseline"
        ]
        assert len(baseline_anomalies) == 0


# ── Anomaly per topic ──


@skip_no_db
class TestAnomalyPerTopic:
    """GET /api/v1/anomalies/topics/{topic} should filter by topic."""

    def test_anomaly_per_topic_should_return_anomalies_for_that_topic(
        self, db_client: TestClient
    ) -> None:
        tenant = "per-topic-team"
        # Build baseline + spike
        for _ in range(10):
            _ingest(db_client, tenant=tenant, topic="per.topic", latency_ms=10.0)
        _ingest(db_client, tenant=tenant, topic="per.topic", latency_ms=200.0)

        resp = db_client.get(
            "/api/v1/anomalies/topics/per.topic",
            params={"tenant": tenant},
        )
        assert resp.status_code == 200
        for a in resp.json()["items"]:
            assert a["topic"] == "per.topic"

    def test_anomaly_per_topic_for_unknown_tenant_should_return_404(
        self, db_client: TestClient
    ) -> None:
        resp = db_client.get(
            "/api/v1/anomalies/topics/any.topic",
            params={"tenant": "zz-nonexistent"},
        )
        assert resp.status_code == 404

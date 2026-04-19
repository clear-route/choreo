"""E2E tests for topic endpoints against real TimescaleDB.

Verifies the full ingest->query cycle: ingest reports with known topics,
then query the topic list, topic latency, and topic runs endpoints.

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


def _ingest(client: TestClient, *, tenant: str = "topic-team", **report_kwargs) -> str:
    """Ingest a report and return the run_id."""
    body = make_report(**report_kwargs)
    resp = client.post(
        "/api/v1/runs",
        json=body,
        headers={
            "X-Chronicle-Tenant": tenant,
            "Idempotency-Key": f"topic-{uuid.uuid4()}",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["run_id"]


# ── Ingest should register topics ──


@skip_no_db
class TestIngestShouldRegisterTopics:
    """Ingesting a report should populate the topics table."""

    def test_ingested_topics_should_appear_in_topic_list(self, db_client: TestClient) -> None:
        _ingest(
            db_client,
            handles=[
                make_handle(topic="orders.created"),
                make_handle(topic="orders.filled"),
            ],
        )

        resp = db_client.get("/api/v1/topics", params={"tenant": "topic-team"})
        assert resp.status_code == 200
        data = resp.json()
        topic_names = [t["topic"] for t in data["items"]]
        assert "orders.created" in topic_names
        assert "orders.filled" in topic_names

    def test_topic_list_should_include_total_count(self, db_client: TestClient) -> None:
        _ingest(
            db_client,
            handles=[make_handle(topic="count.topic")],
        )

        resp = db_client.get("/api/v1/topics", params={"tenant": "topic-team"})
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

    def test_second_ingest_should_update_last_seen_not_duplicate(
        self, db_client: TestClient
    ) -> None:
        _ingest(db_client, handles=[make_handle(topic="dedup.topic")])
        _ingest(db_client, handles=[make_handle(topic="dedup.topic")])

        resp = db_client.get("/api/v1/topics", params={"tenant": "topic-team"})
        topics = [t for t in resp.json()["items"] if t["topic"] == "dedup.topic"]
        assert len(topics) == 1  # not duplicated


# ── Topic list endpoint ──


@skip_no_db
class TestTopicList:
    """GET /api/v1/topics should list topics for a tenant."""

    def test_topic_list_should_return_paginated_response(self, db_client: TestClient) -> None:
        _ingest(db_client, handles=[make_handle(topic="paginated.topic")])

        resp = db_client.get(
            "/api/v1/topics",
            params={"tenant": "topic-team", "limit": 10, "offset": 0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data

    def test_topic_list_for_unknown_tenant_should_return_404(self, db_client: TestClient) -> None:
        resp = db_client.get("/api/v1/topics", params={"tenant": "zz-nonexistent"})
        assert resp.status_code == 404

    def test_topic_list_should_include_latest_run_at(self, db_client: TestClient) -> None:
        _ingest(db_client, handles=[make_handle(topic="time.topic")])

        resp = db_client.get("/api/v1/topics", params={"tenant": "topic-team"})
        topics = [t for t in resp.json()["items"] if t["topic"] == "time.topic"]
        assert len(topics) == 1
        assert topics[0]["latest_run_at"] is not None


# ── Topic latency endpoint ──


@skip_no_db
class TestTopicLatency:
    """GET /api/v1/topics/{topic}/latency should return time-series data."""

    def test_topic_latency_should_return_buckets_with_stats(self, db_client: TestClient) -> None:
        _ingest(
            db_client,
            handles=[
                make_handle(topic="latency.topic", latency_ms=12.5),
                make_handle(topic="latency.topic", latency_ms=15.0),
            ],
        )

        resp = db_client.get(
            "/api/v1/topics/latency.topic/latency",
            params={"tenant": "topic-team"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["topic"] == "latency.topic"
        assert data["resolution"] in ("raw", "hourly", "daily")
        assert isinstance(data["buckets"], list)

        # At least one bucket should have stats
        if data["buckets"]:
            bucket = data["buckets"][0]
            assert "sample_count" in bucket
            assert "p50_ms" in bucket
            assert "p95_ms" in bucket
            assert "p99_ms" in bucket
            assert "slow_count" in bucket
            assert "timeout_count" in bucket
            assert bucket["sample_count"] > 0

    def test_topic_latency_for_unknown_tenant_should_return_404(
        self, db_client: TestClient
    ) -> None:
        resp = db_client.get(
            "/api/v1/topics/any.topic/latency",
            params={"tenant": "zz-nonexistent"},
        )
        assert resp.status_code == 404

    def test_topic_latency_should_accept_resolution_parameter(self, db_client: TestClient) -> None:
        _ingest(
            db_client,
            handles=[make_handle(topic="resolution.topic", latency_ms=10.0)],
        )

        resp = db_client.get(
            "/api/v1/topics/resolution.topic/latency",
            params={"tenant": "topic-team", "resolution": "raw"},
        )
        assert resp.status_code == 200
        assert resp.json()["resolution"] == "raw"

    def test_topic_latency_with_excessive_time_range_should_return_422(
        self, db_client: TestClient
    ) -> None:
        _ingest(db_client, handles=[make_handle(topic="range.topic")])

        resp = db_client.get(
            "/api/v1/topics/range.topic/latency",
            params={
                "tenant": "topic-team",
                "from": "2020-01-01T00:00:00Z",
                "to": "2026-12-31T00:00:00Z",
            },
        )
        assert resp.status_code == 422
        assert "365" in resp.json()["detail"]


# ── Topic runs endpoint ──


@skip_no_db
class TestTopicRuns:
    """GET /api/v1/topics/{topic}/runs should list runs containing a topic."""

    def test_topic_runs_should_return_runs_with_latency_stats(self, db_client: TestClient) -> None:
        run_id = _ingest(
            db_client,
            handles=[make_handle(topic="runs.topic", latency_ms=8.0)],
        )

        resp = db_client.get(
            "/api/v1/topics/runs.topic/runs",
            params={"tenant": "topic-team"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        run_ids = [r["run_id"] for r in data["items"]]
        assert run_id in run_ids

        # Verify latency stats are present
        run_row = next(r for r in data["items"] if r["run_id"] == run_id)
        assert run_row["handle_count"] >= 1
        assert run_row["p50_ms"] is not None
        assert run_row["slow_count"] >= 0

    def test_topic_runs_for_unknown_tenant_should_return_404(self, db_client: TestClient) -> None:
        resp = db_client.get(
            "/api/v1/topics/any.topic/runs",
            params={"tenant": "zz-nonexistent"},
        )
        assert resp.status_code == 404

    def test_topic_runs_should_accept_environment_filter(self, db_client: TestClient) -> None:
        _ingest(
            db_client,
            tenant="env-runs-team",
            environment="staging",
            handles=[make_handle(topic="env.runs.topic")],
        )

        resp = db_client.get(
            "/api/v1/topics/env.runs.topic/runs",
            params={"tenant": "env-runs-team", "environment": "staging"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


# ── Error paths ──


@skip_no_db
class TestTopicErrorCodes:
    """Verify correct HTTP status codes for topic endpoints."""

    def test_topic_list_without_tenant_should_return_422(self, db_client: TestClient) -> None:
        resp = db_client.get("/api/v1/topics")
        assert resp.status_code == 422

    def test_topic_latency_without_tenant_should_return_422(self, db_client: TestClient) -> None:
        resp = db_client.get("/api/v1/topics/any.topic/latency")
        assert resp.status_code == 422

    def test_topic_runs_without_tenant_should_return_422(self, db_client: TestClient) -> None:
        resp = db_client.get("/api/v1/topics/any.topic/runs")
        assert resp.status_code == 422

    def test_invalid_resolution_should_return_422(self, db_client: TestClient) -> None:
        _ingest(db_client, handles=[make_handle(topic="invalid.res")])
        resp = db_client.get(
            "/api/v1/topics/invalid.res/latency",
            params={"tenant": "topic-team", "resolution": "evil"},
        )
        assert resp.status_code == 422

    def test_invalid_tenant_slug_should_return_422(self, db_client: TestClient) -> None:
        resp = db_client.get("/api/v1/topics", params={"tenant": "INVALID SLUG!!"})
        assert resp.status_code == 422

    def test_negative_offset_should_return_422(self, db_client: TestClient) -> None:
        _ingest(db_client, handles=[make_handle(topic="offset.topic")])
        resp = db_client.get("/api/v1/topics", params={"tenant": "topic-team", "offset": -1})
        assert resp.status_code == 422

    def test_zero_limit_should_return_422(self, db_client: TestClient) -> None:
        _ingest(db_client, handles=[make_handle(topic="limit.topic")])
        resp = db_client.get("/api/v1/topics", params={"tenant": "topic-team", "limit": 0})
        assert resp.status_code == 422

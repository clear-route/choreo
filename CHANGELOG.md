# Changelog

All notable changes to the Choreo packages (`choreo`, `choreo-reporter`,
`choreo-chronicle`) are recorded here. Both `choreo` and `choreo-reporter`
release in lockstep under a single `vX.Y.Z` git tag; an entry under a version
heading applies to both unless called out otherwise. `choreo-chronicle` is
versioned independently.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (Chronicle)
- Chronicle reporting server (`choreo-chronicle`) — FastAPI + TimescaleDB + React dashboard.
- Ingest `test-report-v1` JSON via `POST /api/v1/runs` with idempotency support.
- Tenant management with auto-creation on first ingest.
- Per-topic latency analytics with continuous aggregates (hourly, daily).
- Anomaly detection: rolling baseline, budget violation, outcome shift.
- Six dashboard views: Runs, Topics, Topic Drilldown, Reliability, Compare, Anomalies.
- SSE streaming for live dashboard updates.
- Docker Compose for self-hosted deployment.

## [0.1.0] - 2026-04-19

### Added

- **Transport authentication (ADR-0020).** Every real transport accepts a
  typed `auth=` descriptor with optional sync/async resolver for pluggable
  secret stores. Credentials are structurally redacted (`repr`, `pickle`,
  `deepcopy`, pytest assertions all blocked) and cleared from memory after
  `connect()`. Phase 1 ships `NatsAuth` (9 variants), `MockTransport`
  parity, and `safe_url()` query-string redaction.
- Kafka transport (`KafkaTransport`) with `kafka_brokers` allowlist
  enforcement, consumer-group-per-subscribe for broadcast fan-out, and
  `auto_offset_reset=latest` semantics.
- RabbitMQ transport (`RabbitTransport`) with `amqp_brokers` allowlist
  enforcement, topic-exchange routing, and exclusive auto-delete queues.
- Redis transport (`RedisTransport`) with `redis_servers` allowlist
  enforcement, pubsub reader task, and FIFO publish ordering.
- `on().publish()` reply primitive for declaratively scripting reactive
  message flows in the Scenario DSL.
- Pluggable `CorrelationPolicy` with three shipped profiles:
  `NoCorrelationPolicy` (default), `DictFieldPolicy`, and
  `test_namespace()` (ADR-0019).
- Jaeger-style waterfall timeline in the HTML test report.
- Reply lifecycle surfaced in per-test report output.
- Initial `choreo-reporter` pytest plugin (PRD-007) producing HTML + JSON
  test reports with payload redaction and `pytest-xdist` merge support.
- NATS transport with allowlist enforcement on `nats_servers` and a
  `connect_timeout_s` budget that covers the full connect call.
- End-to-end transport contract suite across NATS, Kafka, RabbitMQ, and
  Redis via Docker Compose profiles, including an authenticated NATS
  broker for auth contract tests.
- `pytest-xdist` parallelism on by default; tests isolated via scope
  correlation IDs and UUID-suffixed topics.
- Authentication guide at `docs/guides/authentication.md`.
- Five runnable examples in `examples/` covering hello-world, request-reply,
  parallel isolation, transport auth, and auth resolvers.

### Changed

- `safe_url()` now redacts credential-shaped query-string parameters in
  addition to userinfo. The canonical key set is defined in
  `transports/_auth.py`. Downstream callers that parsed the returned URL
  expecting the query string unchanged will now see `<redacted>` values
  for matching keys.

## [0.0.0] - bootstrap

- Repository initialised.

[Unreleased]: https://github.com/clear-route/choreo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/clear-route/choreo/releases/tag/v0.1.0

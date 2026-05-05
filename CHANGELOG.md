# Changelog

All notable changes to the Choreo packages (`choreo`, `choreo-reporter`,
`choreo-chronicle`) are recorded here. Both `choreo` and `choreo-reporter`
release in lockstep under a single `vX.Y.Z` git tag; an entry under a version
heading applies to both unless called out otherwise. `choreo-chronicle` is
versioned independently.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (`choreo` + `choreo-reporter`) — PRD-013 v1.3 DSL-source attribution

- **Schema v1.3 (additive minor over v1.2).** New optional
  `timeline_entry.source` enum field tagging the DSL surface that
  produced the event: `publish` (test-side `scope.publish` /
  `harness.publish`), `expect` (subscriber registered by `scope.expect`
  / `s.expect`), `reply` (reply chain registered by
  `scope.on(...).publish(...)`), `scope` (scope-level framework event
  such as `DEADLINE`). Disambiguates test-side and reply-chain
  publishes on the same topic. Schema document:
  [test-report-v1.3.json](docs/schemas/test-report-v1.3.json).
- **Renderer additions:** small `hr-waterfall-source` pill rendered
  after each event's action verb naming the DSL surface ("by test",
  "by reply", "by expect", "by scope"). `data-source` attribute on
  every waterfall row for CSS/DOM filtering.
- **Single-Harness preserves byte-identity**: the new optional
  `source` field is omitted entirely when not set, so single-`Harness`
  reports continue to be byte-identical to v1.0/v1.1/v1.2 emission.

### Added (`choreo` + `choreo-reporter`) — PRD-013 Stage timeline capture

- **Schema v1.2 (additive minor over v1.1).** `timeline_entry.transport`
  (optional, regex `^[a-zA-Z0-9_-]{1,64}$`) attributes Stage timeline
  entries to a per-transport child. `timeline_entry.topic` relaxed to
  optional; scope-level events (DEADLINE) omit the field.
  `timeline_entry.logical_topic` is forward-compatibility groundwork
  for translating bridges. Single-`Harness` entries omit all three new
  optional fields, preserving the v1.0/v1.1 byte-identity contract.
  Schema document: [test-report-v1.2.json](docs/schemas/test-report-v1.2.json).
- **Eight Stage timeline hook points** in `choreo` core: `PUBLISHED`,
  `RECEIVED`, `MATCHED`, `MISMATCHED`, `CORRELATION_SKIPPED`,
  `DEADLINE`, `REPLIED`, `REPLY_FAILED`. Per-scope ring buffer (256
  entries); per-run aggregate cap (50,000) at the reporter boundary.
- **`StageScenarioResult.timeline` and `.timeline_dropped`** fields
  exposed to consumers.
- **HTML report additions** (`choreo-reporter`):
  - Stage timeline banner ("Stage timeline captured: N events across
    M transports") for Stage scenarios with non-empty timelines.
  - Per-transport swim lanes (`hr-waterfall-lane[data-transport=...]`)
    in swim-lane mode (Stage scenarios with transport-attributed
    entries).
  - Dedicated scope-events lane (`data-scope-lane="true"`) for
    topic-less events (DEADLINE).
  - Cross-transport reply-arrow SVG overlay (`<path
    data-reply-link-from data-reply-link-to>`) with runtime layout
    pass on boot.
  - Virtualisation for cap-saturated workloads: timelines below 500
    entries mount eagerly; at/above the threshold the renderer mounts
    the first 500 entries and exposes a "Show remaining N events"
    button (`data-virtualised-expand="true"`).
- **Resilience.** `_Timeline.record` swallows internal exceptions (an
  observability seam must never break the AUT) and exposes a
  `record_errors` counter. `Stage.__init__` validates transport names
  against the schema regex (`InvalidTransportNameError`). The
  per-scope timeline seals on `await_all` so late inbound callbacks
  cannot mutate the snapshot's counters.

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

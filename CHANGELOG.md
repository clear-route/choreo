# Changelog

All notable changes to the Choreo packages (`core`, `core-reporter`) are
recorded here. Both packages release in lockstep under a single `vX.Y.Z`
git tag; an entry under a version heading applies to both unless called
out otherwise.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `on().publish()` reply primitive for declaratively scripting reactive
  message flows in the Scenario DSL.
- Jaeger-style waterfall timeline in the HTML test report.
- Reactor / reply lifecycle surfaced in per-test report output.
- Initial `core-reporter` pytest plugin (PRD-007) producing HTML + JSON
  test reports with payload redaction.
- NATS transport with allowlist enforcement on `nats_servers` and a
  `connect_timeout_s` budget that now covers the full connect call rather
  than a single attempt.
- End-to-end transport contract suite runnable against NATS (and ready
  for Kafka / RabbitMQ / Redis brokers via Docker Compose profiles).
- `pytest-xdist` parallelism on by default; tests isolated via scope
  correlation IDs (ADR-0002) and UUID-suffixed topics.

## [0.0.0] - bootstrap

- Repository initialised.

[Unreleased]: https://github.com/clear-route/choreo/compare/HEAD...HEAD

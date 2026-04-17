# Security Policy

## Supported versions

Choreo is pre-1.0. Security fixes ship only against the latest tagged release; there are no long-term support branches.

| Version     | Supported          |
|-------------|--------------------|
| latest tag  | :white_check_mark: |
| older tags  | :x:                |

## Reporting a vulnerability

Please report security issues via GitHub's [Private Vulnerability Reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) on this repository's Security tab. Do not open a public issue for anything exploitable.

We aim to acknowledge within 72 hours and agree a disclosure timeline at first contact. Default embargo is 90 days; shorter if a fix is straightforward, longer if coordinated with a downstream consumer.

Credit is offered at the reporter's choice.

## Threat model

Choreo is a **test-time framework**. It runs inside developer laptops and CI pipelines, opens connections to pub/sub brokers, and emits test reports. The threats we care about:

1. **Information disclosure via test reports** — payloads, error strings, and stack traces can end up in CI artefacts. See *Redaction scope* below.
2. **Accidental production connection** — tests should not reach production brokers. The allowlist guard at `Transport.connect()` (see [ADR-0006](docs/adr/0006-environment-boundary-enforcement.md)) is the primary defence.
3. **Supply-chain** — the framework itself should not be a way to inject code into downstream projects' test suites.

We do **not** model:

- Production runtime — Choreo has no production mode. If you wire it into a production code path you are on your own.
- Confidentiality of test data once a payload is inside a test report that you then publish. The reporter's default redactor is best-effort; see below.

## Redaction scope

`choreo-reporter` ships with a default redactor that:

- Replaces values under a denylist of field names (`password`, `token`, `secret`, `api_key`, `authorization`, and a handful of close variants) with `<redacted>`.
- Scrubs bearer tokens, API keys, and URL credentials (`scheme://user:pass@host`) from string streams (stdout, stderr, log capture, tracebacks).

It does **not**:

- Scan field **values** for secrets — a field named `config` that holds `postgres://user:pw@host/db` is serialised as-is into the report. Add a custom redactor via `register_redactor(...)` for domain-specific shapes.
- Redact structured payload fields you didn't declare — matchers only see what they matched on, but the full payload is captured for debugging.

If you test with PII, credentials, or regulated data, **audit your test reports before shipping them as CI artefacts** and configure a stricter redactor. We will not accept bug reports that amount to "the default redactor did not cover case X" — treat the default as a safety net, not a guarantee.

## Dependency handling

- Runtime dependencies of `choreo` are empty. Transports (`nats-py`, `aiokafka`, `aio-pika`, `redis`) are optional extras.
- `choreo-reporter` pulls `choreo` and `pytest`.
- Upper bounds on all extras prevent unreviewed major-version upgrades. Dependabot is enabled for `pip` and `github-actions`.

If you find a CVE in a transitive dependency that Choreo pulls in by default, file a regular issue; that's not a security report.

# choreo-reporter — pytest plugin for Choreo

Interactive HTML + JSON test reports for the [`choreo`](https://pypi.org/project/choreo/)
message-driven test harness (PRD-007).

Installing this package registers a pytest plugin that, at suite exit, emits a
`test-report/` directory containing:

- An HTML report with a Jaeger-style waterfall of every scenario's messages,
  expectations, replies, and latency budgets.
- A JSON report conforming to the `test-report-v1` schema for CI ingestion.
- Payload redaction for common credential shapes (bearer tokens, URL creds,
  denylisted field names such as `password` / `token` / `api_key`).
- `pytest-xdist` merge support for parallel runs.

## Install

```bash
pip install choreo-reporter
```

Once installed, the plugin loads automatically on the next `pytest` run.

## Configuration

```ini
# pytest.ini / pyproject.toml
[pytest]
addopts = --harness-report=test-report
```

Disable with `--harness-report-disable`. Register a custom redactor for
domain-specific payload shapes via `choreo_reporter.register_redactor(...)`.

## Documentation

See the project README at
<https://github.com/clear-route/choreo> for the full architecture, the
Scenario DSL, and the report schema.

## Licence

Apache-2.0. See [LICENSE](LICENSE).

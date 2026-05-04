# choreo-reporter — pytest plugin for Choreo

Interactive HTML + JSON test reports for the [`choreo`](https://pypi.org/project/choreo/)
message-driven test harness (PRD-007).

Installing this package registers a pytest plugin that, at suite exit, emits a
`test-report/` directory containing:

- An HTML report with a Jaeger-style waterfall of every scenario's messages,
  expectations, replies, and latency budgets.
- A JSON report conforming to the `test-report-v1.1` schema for CI ingestion
  (see [docs/schemas/test-report-v1.1.json](../../docs/schemas/test-report-v1.1.json)).
- Payload redaction for common credential shapes (bearer tokens, URL creds,
  denylisted field names such as `password` / `token` / `api_key`).
- Hash-based redaction of multi-transport (`Stage`) per-transport
  correlation ids at the report boundary via
  `choreo.redaction.redact_correlation_id` (PRD-012 §1.5.1). Internally
  the framework calls these "wire ids" (the bridge's `to_wire` output);
  at the report boundary they surface as `correlation_id` for
  consistency with `Handle.correlation_id`.
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

## Multi-transport (`Stage`) scenarios — PRD-012 v1.1

Stage scenarios (multi-transport bridges; see [ADR-0027](../../docs/adr/0027-stage-multi-transport-coordinator.md))
land in the report as additive optional fields. Single-`Harness` reports are
byte-identical to the v1.0 emission aside from the `schema_version` value.

### What's new in `results.json`

The schema bumps from `"1"` to `"1.1"` (additive minor per PRD-007 §US-3).
Consumers gating on `schema_version.startswith("1")` continue to work; strict
schema-validator consumers update their pinned schema document to
[test-report-v1.1.json](../../docs/schemas/test-report-v1.1.json). The v1.0
schema remains in tree at
[test-report-v1.0.json](../../docs/schemas/test-report-v1.0.json) for
consumers pinned to it.

Additive fields:

- `handle.transport` — present on Stage handles only; the registered
  transport name (`"nats"`, `"kafka"`, ...). Single-`Harness` handles
  omit the key entirely.
- `handle.correlation_id` — present on Stage handles only; the
  per-transport correlation id (framework internal: "wire id"),
  **hash-redacted** as `"sha256:<16 hex chars>"`. Single-`Harness`
  handles do not carry per-handle correlation_id (the scenario-level
  `correlation_id` covers them, un-redacted).
- `reply_report.trigger_transport` and `reply_report.response_transport` —
  present on Stage replies only.
- `scenario.stage` — optional block; presence is the canonical signal that
  the scenario was a Stage scenario:

  ```json
  "stage": {
    "bridge_class": "MappedBridge",
    "transports": ["kafka", "nats"],
    "correlation_ids": {
      "nats":  "sha256:3f2a91b8c4d50e1f",
      "kafka": "sha256:7e8b50c1ad4912ff"
    }
  }
  ```

- `run.transport` is now nullable; `run.transports` is a new optional
  array. Single-`Harness`-only runs continue to emit `transport`; runs
  carrying any Stage scenario emit `transport: null` and `transports`
  with the sorted union of every transport name observed.
- `run.redactions.redaction_version` — the algorithm version that
  produced the hash-redacted values (currently `"v1"`). Emitted only
  for runs that contain at least one Stage scenario.

Reply state remains the v1.0 four values (`replied`, `reply_failed`,
`armed_no_match`, `armed_matcher_mismatched`); `StageReplyState.FIRED`
maps to `"replied"` and `StageReplyState.FIRED_BUILDER_ERROR` maps to
`"reply_failed"`. No enum extension.

The framework's `StageReplyReport.response_topic` is serialised under
the existing `reply_topic` JSON key (PRD-012 §1.2.1) so consumers
already querying `reply_report.reply_topic` continue to work.

### Migration paths for v1.0 → v1.1

Three migration scenarios — pick the one that matches your consumer:

1. **Strict-validator consumer pinned to `test-report-v1.0.json`**: update
   the pinned schema to `test-report-v1.1.json`. The diff is purely
   additive; every v1.0-valid report (after the `schema_version`
   substitution `"1"` → `"1.1"`) validates against v1.1.
2. **Lenient consumer that ignores unknown fields**: gate on
   `schema_version.startswith("1")` to accept both v1.0 and v1.1.
   No code change required; the new fields are ignorable additions.
3. **Routing-logic consumer reading `run.transport` directly** (e.g.
   `if report["run"]["transport"] == "MockTransport": ...`): for any
   run containing a Stage scenario, `run.transport` is now `null`.
   Guard against `None` and check `run.transports` membership for the
   full set of transports observed in the run:

   ```python
   transport = report["run"].get("transport")
   transports = report["run"].get("transports") or ([transport] if transport else [])
   if "MockTransport" in transports:
       ...
   ```

   The `transports` array is the union (sorted alphabetically) of every
   transport name the run touched, including the single-`Harness`
   transport class name when single-`Harness` and Stage scenarios mix.

### HTML report — `data-*` contract

The HTML report's `data-*` attributes split into two tiers (PRD-012 §3.6):

**Stable tier** (snapshot-tested; changes warrant a deprecation
process):

| Attribute | Element | Value |
|---|---|---|
| `data-schema-version` | `.harness-report` root | `"1.1"` |
| `data-grouping-mode` | `.harness-report` root | stable; today emits `"by-scenario"` only (the toggle UI that flips it to `"by-transport"` is a follow-up — the attribute name and initial value are pinned now) |
| `data-handle-transport` | handle row | transport name |
| `data-stage-transports` | Stage breadcrumb container | space-separated transports |
| `data-stage-transport` | breadcrumb pill / wire-id pill | transport name |
| `data-reply-trigger-transport` | reply row | transport name |
| `data-reply-response-transport` | reply row | transport name |
| `data-reply-publish-failed` | reply row | `"true"` / `"false"` (Stage replies only) |
| `data-failing-transports` | scenario header sub-badge | space-separated transports |

**Advisory tier** (debugging convenience; may change without a schema bump):

| Attribute | Element |
|---|---|
| `data-stage-bridge-class` | Stage breadcrumb container |
| `data-failing-reply-response-transport` | scenario header sub-badge |

Consumer reliance on advisory-tier attributes is at consumer risk.

### Wire-id redaction at the report boundary

The framework's in-process `_redact()` (head=8, tail=4, length annotation;
defined in `choreo.stage`) is preserved for short-lived error messages.
The on-disk `results.json` boundary uses **hash-based redaction** via
`choreo.redaction.redact_wire_id` — SHA-256 truncated to 16 hex chars,
prefixed `sha256:`. The two are deliberately decoupled: error messages
need to be human-debuggable in the moment; archived reports need to
resist greppable correlation across months of retention.

Algorithm version is emitted in `run.redactions.redaction_version`. A
future tightening of the algorithm bumps the version string; consumers
detect the change via this field.

**Note:** redaction strength assumes the correlation id has at least 64 bits of
entropy. The shipped `IdentityBridge` and `MappedBridge` use
`secrets.token_hex(16)` (64 bits) for `bridge.fresh()` by default. A
custom bridge that derives `fresh()` from low-entropy sources (request
id, sequence number, customer id) defeats redaction — see ADR-0027
§Security Considerations.

## Documentation

See the project README at
<https://github.com/clear-route/choreo> for the full architecture, the
Scenario DSL, and the report schema.

- [PRD-007 — Test Report Output](../../docs/prd/PRD-007-test-report-output.md)
- [PRD-012 — Test Report Stage Support](../../docs/prd/PRD-012-test-report-stage-support.md)
- [Stage user guide](../../docs/guides/stage.md) — §"Reading the test report"

## Licence

Apache-2.0. See [LICENSE](LICENSE).

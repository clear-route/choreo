# 03 — Parallel isolation with a `CorrelationPolicy`

The library default is `NoCorrelationPolicy`: transparent passthrough, no
stamping, no filter. Every live scope on a topic sees every message.

That's fine for a single-scenario test. It **isn't** fine when two
scenarios run concurrently on a shared broker (or sequentially against
one that carries state between tests) — without correlation routing,
scenario A's publish will satisfy scenario B's expectation on the same
topic.

This example shows the problem and the fix.

## Run it

```bash
pytest examples/03-parallel-isolation/
```

## What's going on

**`test_without_a_policy_parallel_scopes_see_each_others_messages`** —
illustrative. Two nested scopes both match scope A's publish because
there's nothing separating them.

**`test_with_a_policy_parallel_scopes_only_match_their_own_messages`** —
the fix: pass `correlation=DictFieldPolicy(field="correlation_id")` at
Harness construction. The harness stamps a per-scope id onto outbound
dicts, and the inbound filter drops messages whose id doesn't match the
scope's own.

**`test_the_policy_can_stamp_any_field_name_your_schema_uses`** — the
`field` parameter lets the policy stamp and read whatever your schema
calls the correlation field (`trace_id`, `request_id`, an OpenTelemetry
span id).

**`test_explicit_correlation_on_the_payload_is_honoured_over_the_policy_default`** —
`setdefault`-style stamping. If the caller supplies the field, the policy
leaves it alone — useful for echoing a SUT-supplied id back on a reply,
or negative tests that deliberately impersonate another scope.

**`test_a_policy_with_a_prefix_refuses_foreign_namespace_ids`** — a
policy configured with `prefix="TEST-"` refuses any explicit override
that doesn't start with that prefix. Reproduces the pre-ADR-0019 captive
behaviour; ships as the `test_namespace()` factory for convenience.

## When do I want a policy?

| Your situation | Pick |
|---|---|
| MockTransport only, one scenario at a time | `NoCorrelationPolicy` (default). |
| Parallel scenarios sharing one broker connection | `DictFieldPolicy(field=...)`. |
| Parallel scenarios on shared infrastructure (multi-tenant, shared CI broker) | `DictFieldPolicy(field=..., prefix=...)` with a run-unique prefix. |
| Downstream systems filter test traffic on `TEST-` prefix | `test_namespace()`. |
| Your schema carries correlation in a header, not the payload | Write a `CorrelationPolicy` subclass — the shape (`new_id` / `write` / `read`) works for payload or header equally. |

See [ADR-0019](../../docs/adr/0019-pluggable-correlation-policy.md) for
the full protocol contract and the trust-boundary rules (consumer code
running inside the harness hot path).

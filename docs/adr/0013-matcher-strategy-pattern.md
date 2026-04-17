# 0013. Matcher Strategy Pattern for Inbound Message Matching

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-002 — Scenario DSL](../prd/PRD-002-scenario-dsl.md) §Key Components / Matcher

---

## Context

Every `scenario.expect(topic, matcher)` call registers a predicate over inbound messages: "when a message arrives on `topic` whose field `status` equals `PASS`, fire the handle." Payloads come in multiple shapes — JSON-like dicts, protobuf-decoded namespaces, tag-value maps. Matchers must work across these without forcing the test author to write protocol-specific code.

The naive option is one callable per test: `expect(topic, lambda m: m["status"] == "PASS")`. That works but does not compose, does not print a useful message on mismatch, and pushes payload-shape knowledge into every test. This ADR records the decision to use the **Strategy pattern** — a small set of composable `Matcher` implementations behind a common interface.

### Background

- [framework-design.md §8](../framework-design.md) and [PRD-002 §Key Components](../prd/PRD-002-scenario-dsl.md) both specify the Strategy pattern.
- [ADR-0002](0002-scoped-registry-test-isolation.md) and [ADR-0004](0004-dispatcher-correlation-mediator.md) treat matchers as opaque predicates — this ADR defines the actual shape.
- Precedents: Hamcrest (Java), `pytest.approx`, `expect.js`, WireMock's `matching()` builders — all use composable matcher strategies.

### Problem Statement

What interface do matchers implement, what set of built-in matchers does the framework ship, and how do matchers compose?

### Goals

- **Single interface.** All matchers implement the same `Matcher` Protocol; they are interchangeable in `expect(...)` calls.
- **Composable.** `all_of(a, b)`, `any_of(a, b)` allow Boolean composition without authors hand-rolling callbacks.
- **Informative on failure.** When a matcher rejects a message, the failure message names the matcher, the expected value, and the actual value.
- **Protocol-agnostic default set.** Built-in matchers work on dict-like payloads. Tag-value matchers live alongside (integer-keyed lookups) without changing the base Protocol.
- **Safe-by-default.** Matchers are pure functions of the message; no side effects, no external state.

### Non-Goals

- A full expression language for matching (JSONPath, XPath). Simple matchers cover 95% of cases; complex cases write a custom matcher.
- Matchers that transform the message. Matchers return bool, not new values.
- Async matchers. Matchers run synchronously on the dispatcher thread.

---

## Decision Drivers

- **Composability.** `all_of(field_equals("status", "PASS"), field_gt("qty", 0))` should read as a test assertion.
- **Debuggability.** A failed match produces a clear "expected X, got Y" line.
- **Extensibility.** Custom matchers slot into the same interface; users do not touch framework code.
- **Simplicity.** Keep the built-in set small and cover the common cases.

---

## Considered Options

### Option 1: Strategy pattern with Protocol interface and composition helpers (chosen)

**Description:** `Matcher` is a Protocol with a single method `match(payload) -> MatchResult` (where MatchResult carries both the bool and a reason string). Built-in matchers implement it: `FieldEquals`, `FieldIn`, `FieldGt`, `FieldExists`, `GroupContains`, `PayloadContains`. Composition helpers `all_of`, `any_of`, `not_` return compound matchers.

**Pros:**
- One interface to learn; custom matchers plug in.
- Composition via `all_of` / `any_of` reads as a boolean expression.
- Each matcher carries its own description; failure messages are specific.
- Pure-function semantics are easy to test in isolation.

**Cons:**
- Class per matcher is more code than a function. Acceptable: matchers are small.
- Composition helpers return wrapper classes that have to carry descriptions correctly.

### Option 2: Bare callable — `Callable[[Payload], bool]`

**Description:** A matcher is any function `fn(payload) -> bool`.

**Pros:**
- Simplest possible.
- Lambdas in tests.

**Cons:**
- Composition is awkward: `lambda m: a(m) and b(m)` nested three deep is unreadable.
- Zero diagnostic information on failure — the test just times out or logs "matcher returned False".
- Every test reinvents the payload-shape handling.

### Option 3: JSONPath / JMESPath / similar expression language

**Description:** Matchers are strings: `"$.status == 'PASS' && $.qty > 0"`.

**Pros:**
- Compact.
- Familiar to people who've used JSONPath.

**Cons:**
- Parsing a string at test-registration time pushes errors to runtime.
- Protobuf / tag-value payloads need path languages per protocol.
- Debugging an expression string is harder than debugging Python code.
- Overkill for the simple matchers that dominate.

### Option 4: dataclass-based matchers with rich equality

**Description:** Matchers are dataclasses; match is `payload == MatcherInstance`.

**Pros:**
- Declarative feel.

**Cons:**
- `__eq__` tricks surprise readers.
- Hard to compose Boolean-style.
- Not how the rest of the harness expresses logic.

---

## Decision

**Chosen Option:** Option 1 — Strategy pattern with Protocol interface and `all_of` / `any_of` / `not_` composition helpers.

### Rationale

- Matches the precedent in Hamcrest, WireMock, pytest, and the rest of the integration-test world; readers recognise the pattern immediately.
- Composition helpers are the only thing we need to turn a small primitive set into an expressive vocabulary.
- Diagnostic messages come for free — each matcher owns its description.
- Option 2 loses composability and diagnostics; Option 3 is a parser masquerading as a simplicity gain; Option 4 is non-idiomatic.

---

## Consequences

### Positive

- All matchers share the same lifetime, invocation pattern, and diagnostic format.
- Custom matchers slot in without framework changes — implement the Protocol, done.
- Composition reads naturally: `all_of(field_equals("status", "PASS"), field_gt("qty", 0))`.
- Failure messages are specific and informative; timeouts carry "here is what did not match" context.

### Negative

- A dozen small classes to document. Acceptable: one per named matcher.
- Composition wrappers add one layer of indirection per `all_of`.
- A user writing a custom matcher must remember to populate the description.

### Neutral

- Matcher instances are reusable — one `field_equals("status", "PASS")` can be used in many scenarios.
- Matchers have no mutable state; equality is by composition + args.
- No runtime-registration mechanism; custom matchers are imported like any other class.

### Security Considerations

- **Matchers run on the dispatcher thread.** A slow or blocking matcher freezes inbound dispatch for every concurrent scenario. Matchers should complete in microseconds; anything longer is a bug in the matcher.
- **Matchers do not have access to credentials or other scopes' state.** They receive only the decoded payload.
- **Custom matchers could in principle exfiltrate data** via side effects. The Protocol says matchers are pure; a framework-internal lint rule checks that matcher classes have no `__init__` side effects and no module-level state access.

---

## Implementation

### Protocol

```
class Matcher(Protocol):
    description: str
    def match(self, payload: Any) -> MatchResult: ...


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    reason: str   # e.g. "status == 'PASS'" or "expected status == 'PASS', got 'FAIL'"
```

### Built-in matchers (day one)

| Name | Shape | Purpose |
|------|-------|---------|
| `field_equals(path, value)` | `FieldEquals` | `payload[path] == value` |
| `field_in(path, values)` | `FieldIn` | `payload[path] in values` |
| `field_gt(path, value)` | `FieldGt` | `payload[path] > value` |
| `field_lt(path, value)` | `FieldLt` | `payload[path] < value` |
| `field_exists(path)` | `FieldExists` | `path in payload` |
| `group_contains(group_path, entry_matcher)` | `GroupContains` | Any entry in a repeating group matches |
| `payload_contains(substring)` | `PayloadContains` | Raw-bytes substring (escape hatch) |
| `contains_fields(spec)` | `ContainsFields` | Recursive subset match over a nested dict/list spec; leaves may be literals or embedded matchers |
| `eq(value)` | `Eq` | `payload == value` (pathless; for use at a leaf inside `contains_fields`) |
| `in_(values)` | `In` | `payload in values` (pathless) |
| `gt(value)` | `Gt` | `payload > value` (pathless) |
| `lt(value)` | `Lt` | `payload < value` (pathless) |
| `all_of(*matchers)` | `AllOf` | All must match |
| `any_of(*matchers)` | `AnyOf` | At least one must match |
| `not_(matcher)` | `Not` | Inverse |

### Path semantics

- For dict payloads: `path` is a dotted string, traversing keys. `"orders.0.status"` → `payload["orders"][0]["status"]`.
- For tag-value payloads: `path` is an int (tag number), traversing `payload[tag]`.
- For protobuf payloads: Matchers operate on the decoded dict form (test fixtures typically decode before matching). Custom matchers handle raw protobuf if needed.

Matchers accept both dict and int paths; the built-ins dispatch on type.

Inside `contains_fields`, a spec leaf may be either a literal (compared with
`==`) or a `Matcher` instance, in which case the matcher is applied to the
sub-payload at that position. Matchers compose at any depth, so
`contains_fields({"order": {"qty": gt(0), "state": in_(("NEW","PART"))}})`
reads as a single document-shaped assertion. The pathless value-matchers
(`eq`, `in_`, `gt`, `lt`) exist so leaves stay readable; `all_of` / `any_of` /
`not_` / a user-defined `Matcher` work at a leaf without any special case.

### Failure message format

`expected {matcher.description}, got payload={payload!r}`. Matchers carry their own description; composed matchers build the description recursively (`all_of(A, B)` → `"all of [A.description, B.description]"`).

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (with PRD-002 implementation): core matchers — `field_equals`, `field_in`, `field_gt`, `field_exists`, `payload_contains`, `all_of`, `any_of`, `not_`.
- Phase 2: `group_contains`, `field_lt`, `field_startswith`, `field_matches_regex`.
- Phase 3: tag-number specific helpers + any custom matchers discovered during real use.

---

## Validation

### Success Metrics

- **Every matcher's failure message names the matcher + expected + actual.** Verified by framework-internal tests.
- **Composition depth of 3 works and is readable** (`all_of(any_of(a, b), c)`). Verified by scenario tests.
- **Zero custom matchers require touching framework code.** Custom matchers defined in tests compose with built-ins by implementing the Protocol.
- **No matcher takes longer than 1ms per match.** Verified by a perf test that runs each built-in 10k times.

### Monitoring

- No runtime metrics — matchers are pure functions.
- Framework-internal lint flags matcher classes whose `__init__` has side effects beyond field assignment.

---

## Related Decisions

- [ADR-0002](0002-scoped-registry-test-isolation.md) — Scoped registry (matchers run inside a scope's inbound handling).
- [ADR-0004](0004-dispatcher-correlation-mediator.md) — Dispatcher (matchers run on the dispatcher thread via the LoopPoster).
- [ADR-0012](0012-type-state-scenario-builder.md) — DSL (consumes matchers via `expect*()`).
- [ADR-0014](0014-handle-result-model.md) — Handles (hold the MatchResult for per-expectation assertions).

---

## References

- [PRD-002 §Key Components / Matcher](../prd/PRD-002-scenario-dsl.md)
- [framework-design.md §10 Cross-cutting patterns — Strategy](../framework-design.md)
- Hamcrest documentation — precedent for composable matcher strategies
- pytest.approx — a Strategy-pattern matcher in the Python ecosystem

---

## Notes

- **Open follow-up — tag-value path semantics.** Tag-value payloads (integer-keyed) may benefit from a dedicated helper set (`tag_equals(11, "...")`) rather than dict-path access. **Owner:** Framework maintainers.
- **Open follow-up — matcher caching.** A composed matcher that is re-evaluated many times per scenario could cache intermediate results. Only matters if matchers turn out to be expensive. Revisit if profiling shows the need. **Owner:** Platform.

**Last Updated:** 2026-04-17 — extended `contains_fields` to accept `Matcher`
instances at any position in the spec; added pathless value-matchers `eq`,
`in_`, `gt`, `lt` for ergonomic leaves.

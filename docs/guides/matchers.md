# Matchers — a cookbook

Matchers are the predicates you hand to `Scenario.expect(...)` to say what
a reply should look like. Every matcher implements the same small contract
(`match(payload) → MatchResult`), so they compose freely and a bad match
always gives you a human-readable reason — never a bare `assert False`.

This guide is task-oriented: pick the shape of what you want to check,
find the matching section, copy the pattern. For the rationale behind the
design, see [ADR-0013](../adr/0013-matcher-strategy-pattern.md). For the
exact signatures, the docstrings on
[packages/core/src/choreo/matchers.py](../../packages/core/src/choreo/matchers.py) are canonical.

---

## The 30-second version

```python
from choreo.matchers import (
    field_equals, field_in, field_gt,
    contains_fields, eq, gt, in_, exists, matches,
    all_of, any_of, not_, every, any_element,
)

# Flat: one assertion per field-path.
s.expect("events.validated", field_equals("status", "PASS"))

# Nested: one assertion, one document shape.
s.expect("state.changed", contains_fields({
    "item": {
        "status":   "COMPLETED",                      # literal
        "count":    gt(0),                            # matcher at a leaf
        "stage":    in_(("NEW", "RUNNING", "DONE")),
        "id":       matches(r"^ITEM-\d+$"),           # regex
        "trace_id": exists(),                         # presence only
        "entries":  any_element(field_gt("px", 0.0)), # list quantifier
    },
    "actor": not_(in_(("blocked_1", "blocked_2"))),
}))
```

Rule of thumb: reach for a `field_*` matcher when you only care about
one field; reach for `contains_fields` when the shape of the reply is
itself the assertion; reach for `every` / `any_element` when the
assertion is about a list.

---

## Matching a single flat field

Every `field_*` matcher takes a path and a value. Paths traverse dicts
with `.` and index into lists with integers:

```python
from choreo.matchers import (
    field_equals, field_ne, field_in, field_gt, field_lt,
    field_exists, field_matches,
)

field_equals("status", "PASS")                     # payload["status"] == "PASS"
field_equals("item.status", "COMPLETED")           # nested dict key
field_equals("entries.0.amount", 100.25)           # list index
field_equals("item.entries.1.count", 250)          # mixed nesting

field_ne("status", "REJECTED")                     # not equal
field_in("reason", ("CREDIT", "LIMIT"))            # membership
field_gt("count", 0)                               # strictly greater
field_lt("count", 1_000_000)                       # strictly less
field_exists("record_id")                          # key present (any value)
field_matches("order_id", r"^ORD-\d+$")            # regex fullmatch on strings
```

For integer-keyed payloads (tag-value protocols), use the integer directly
as the path:

```python
field_equals(35, "D")                              # tag 35 == "D"
field_equals(11, "REQ-123")                        # tag 11 == "REQ-123"
```

Missing fields and out-of-range indices are treated as a non-match with a
`"field 'x' missing"` reason, never a `KeyError`.

### Paths that contain a dot, and other ambiguities

The dotted-string form is sugar: segments that look like integers are
treated as list indices, and any literal `"."` inside a key cannot be
reached. The **sequence form** is the canonical path, and the only way
to express either:

```python
field_equals(("trace.id",), "abc")          # dict key with a dot
field_equals(("items", "0"), "x")           # string key "0" on a dict
field_equals(("items", 0), "x")             # list index 0 — distinguishable
field_equals(("fills", 0, "price"), 100.25) # mixed nesting, no ambiguity
```

Dotted strings still work for everything else — use them when your keys
are simple identifiers.

---

## Matching nested structures

`contains_fields(spec)` does a recursive subset match. Every key/value in
your spec must appear in the payload at the same position; extra keys
anywhere in the payload are fine.

```python
from choreo.matchers import contains_fields

contains_fields({
    "item": {
        "status":   "COMPLETED",
        "amount":   100.25,
    },
    "actor": "alice",
})
```

Lists match **positionally**, and the payload may be longer than the spec:

```python
contains_fields({"entries": [{"amount": 100.25}]})
# matches   {"entries": [{"amount": 100.25, "count": 500}, {"amount": 100.50}]}
# matches   {"entries": [{"amount": 100.25}]}
# fails     {"entries": []}                         (spec longer than payload)
```

---

## Matching deep inside structures

A spec leaf can be a literal **or** a matcher. When the value at a
position is a matcher, it's handed the sub-payload at that position.
This is how you do comparisons, ranges, or set-membership without
flattening everything back to top-level `all_of(field_*(...), ...)`.

```python
from choreo.matchers import contains_fields, eq, gt, lt, in_, all_of, not_

contains_fields({
    "item": {
        "status":   "COMPLETED",                       # literal
        "count":    gt(0),                             # numeric comparison
        "stage":    in_(("NEW", "RUNNING", "DONE")),   # set membership
        "amount":   all_of(gt(0.0), lt(10_000.0)),     # range via composition
    },
    "entries": [
        {"stage": in_(("NEW", "RUNNING"))},            # matchers work at list indices too
    ],
    "actor": not_(in_(("blocked_1", "blocked_2"))),
})
```

The pathless value matchers — `eq`, `ne`, `in_`, `gt`, `lt`, `matches`,
`exists` — exist for this use only. At top level you'd write
`field_gt("item.count", 0)`; inside `contains_fields` you write `gt(0)`
because `contains_fields` is already walking the path for you. `exists()`
is particularly useful as a presence assertion that doesn't constrain the
value: `{"trace_id": exists()}` accepts any non-missing value, including
`None`.

---

## Composing matchers

Five combinators work at any level — top-level or embedded inside
`contains_fields`:

```python
from choreo.matchers import all_of, any_of, not_, every, any_element

all_of(m1, m2, m3)      # every child must match
any_of(m1, m2)          # at least one child must match
not_(m)                 # inverse
every(m)                # every element of a list payload passes m
any_element(m)          # at least one element of a list payload passes m
```

The list quantifiers turn "there exists a fill with px > 100" into one
line inside a shape match:

```python
contains_fields({"order": {"fills": any_element(field_gt("px", 100.0))}})
```

A quantifier on a non-list payload fails with a `type_mismatch` reason
— it does not silently succeed on a dict.

Real example — "the job completed fully or partially, and it was not
one of the two blocked actors":

```python
s.expect("jobs.updated", all_of(
    any_of(
        field_equals("stage", "DONE"),
        field_equals("stage", "PART"),
    ),
    not_(field_in("actor", ("blocked_1", "blocked_2"))),
))
```

The same logic reads better nested inside a shape assertion:

```python
s.expect("jobs.updated", contains_fields({
    "stage": in_(("DONE", "PART")),
    "actor": not_(in_(("blocked_1", "blocked_2"))),
}))
```

Composition depth of three (e.g. `all_of(any_of(a, b), c)`) is the
readable limit. Beyond that, promote the inner piece to a named
matcher — see below.

---

## The raw-bytes escape hatch

Use `payload_contains(substring)` only when you cannot decode the
payload for some reason (wrong codec, binary handshake, garbled test
fixture). It searches the raw bytes before any decode step.

```python
from choreo.matchers import payload_contains

payload_contains(b"MAGIC-HEADER")      # protocol-level probe
```

`payload_contains` **requires a `bytes` payload** and raises `TypeError`
when handed a decoded dict or string. If your payload has already been
decoded, use `field_matches` or `contains_fields` instead — the error
points you at the right tool rather than silently stringifying a
megabyte dict.

Avoid this for anything you could express as a field check; the failure
message is less useful, and it defeats the point of the codec layer.

---

## Writing your own matcher

The `Matcher` Protocol has two required pieces — a `match(payload) →
MatchResult` method and a `description` attribute. A frozen dataclass is
the canonical shape; any object matching the duck-type will work
(including inside `contains_fields`).

```python
from dataclasses import dataclass
from choreo.matchers import MatchResult, contains_fields

@dataclass(frozen=True)
class IsEven:
    description: str = "is_even"

    def match(self, payload: object) -> MatchResult:
        if isinstance(payload, int) and payload % 2 == 0:
            return MatchResult(True, f"{payload} is even")
        return MatchResult(False, f"{payload!r} is not even")


s.expect("jobs.updated", contains_fields({"item": {"count": IsEven()}}))
```

Two rules for custom matchers, from ADR-0013 §Security Considerations:

1. **Pure.** No I/O, no mutable module-level state, no side effects on
   the payload. Matchers run on the dispatcher thread; a slow matcher
   freezes inbound routing for every concurrent scenario.
2. **Microsecond-cheap.** If you need to call out to a service, do it
   before/after the scenario and pass the result as a literal.

An `expected_shape()` method is optional but recommended — it lets the
test-report writer render a machine-readable version of what you
expected (PRD-007). This is a separate `Reportable` Protocol, split from
`Matcher` per the interface-segregation principle: a matcher that omits
`expected_shape()` is a perfectly valid `Matcher`, and the report falls
back to `description`.

If your custom matcher composes its children into a `MatchFailure` with
its own `children`, the walker in `contains_fields` will automatically
reroot every `<root>` / `all_of` / `any_of` path inside the tree to the
walked position — so a user matcher embedded at `order.qty` reports
failures under `order.qty`, not at the matcher's private root.

---

## Reading failure messages

Every non-match carries a reason string. Three shapes you'll see:

**Flat field mismatch:**
```
expected status='PASS', got 'FAIL'
```

**Composed failure — names the failing child:**
```
all_of failed on count > 0: expected count > 0, got -1
```

**Nested failure — names the full path:**
```
at item.entries.0.count: expected > 0, got -1
```

That last shape is the payoff for using `contains_fields` with embedded
matchers: the path `item.entries.0.count` is assembled from the walk,
and the predicate's own description (`> 0`) bubbles up. You know *what*
failed and *where* without reading the payload.

Near-miss vs. silent timeout is handled one layer up by the Handle
result model (ADR-0014): if messages arrived on your correlation but
failed every matcher, `ScenarioResult.assert_passed()` tells you it was
a **near-miss** (expectation bug) rather than a **silent timeout**
(routing bug).

---

## Reference

### Field-path matchers (top-level)

| Matcher | Checks |
|---|---|
| `field_equals(path, value)` | `payload[path] == value` |
| `field_ne(path, value)` | `payload[path] != value` |
| `field_in(path, values)` | `payload[path] in values` |
| `field_gt(path, value)` | `payload[path] > value` |
| `field_lt(path, value)` | `payload[path] < value` |
| `field_exists(path)` | the key at `path` is present |
| `field_matches(path, pattern)` | regex fullmatch on a string at `path` |

Paths accept `str` (dotted-sugar), `int` (single-level lookup), or a
sequence of `str`/`int` (canonical form; the only way to reach a key
that contains `"."` or to disambiguate a string key from a list index).

### Pathless value matchers (for leaves inside `contains_fields`)

| Matcher | Checks |
|---|---|
| `eq(value)` | `payload == value` |
| `ne(value)` | `payload != value` |
| `in_(values)` | `payload in values` |
| `gt(value)` | `payload > value` |
| `lt(value)` | `payload < value` |
| `matches(pattern)` | regex fullmatch on a string payload |
| `exists()` | the sub-payload is present (any value, including `None`) |

### Structural and composition matchers

| Matcher | Checks |
|---|---|
| `contains_fields(spec)` | recursive subset match; leaves may be literals or matchers |
| `all_of(*matchers)` | every child matches |
| `any_of(*matchers)` | at least one child matches |
| `not_(matcher)` | inverse |
| `every(matcher)` | every element of a list payload passes `matcher` |
| `any_element(matcher)` | at least one element of a list payload passes `matcher` |
| `payload_contains(bytes)` | raw-bytes substring (bytes-only; raises `TypeError` otherwise) |

---

## Where to go next

- [ADR-0013](../adr/0013-matcher-strategy-pattern.md) — why the Strategy
  pattern, what was considered, what the Protocol contract is.
- [ADR-0014](../adr/0014-handle-result-model.md) — how match results
  become PASS/FAIL/TIMEOUT outcomes on Handles.
- [docs/prd/PRD-002-scenario-dsl.md](../prd/PRD-002-scenario-dsl.md) —
  how `expect → publish → await_all` threads matchers into the scenario.
- [packages/core/src/choreo/matchers.py](../../packages/core/src/choreo/matchers.py) — the source.
  Every matcher is a short frozen dataclass; reading one is the fastest
  way to see how to write another.
- [packages/core/tests/test_matchers.py](../../packages/core/tests/test_matchers.py) — one
  behaviour-named test per matcher shape. Good reference for the kinds
  of payloads each matcher is happy with.

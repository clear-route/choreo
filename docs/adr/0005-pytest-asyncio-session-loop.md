# 0005. Session-Scoped Event Loop via pytest-asyncio `loop_scope`

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-001 — Framework foundations](../prd/PRD-001-framework-foundations.md) — pytest-asyncio integration

---

## Context

[ADR-0001](0001-single-session-scoped-harness.md) commits the harness to a single session-scoped Harness owning long-lived transports. That commitment has a specific consequence in pytest-asyncio: **the event loop must also be session-scoped**, or we hit the classic `RuntimeError: got Future attached to a different loop`.

pytest-asyncio's default behaviour (as of v0.24+) is to give each test a fresh function-scoped event loop. A `scope="session"` fixture paired with default-scope tests sees a different loop than its consumers — the Harness is built on loop A, the test runs on loop B, the Harness's Futures are invalid in the test.

This is a known footgun documented in the pytest-asyncio issues (#944, #950) and has broken many async integration-test frameworks over the years. We need to decide explicitly how we handle it.

### Background

- pytest-asyncio v0.24 introduced `loop_scope` as a first-class concept, independent of fixture scope. Before v0.24, the common workaround was to override the `event_loop` fixture to session scope — now deprecated with warnings.
- [framework-design.md §3](../framework-design.md) calls this out under the Option A recommendation for connection reuse.
- [framework-design.md §5](../framework-design.md) (timeout enforcement) commits the framework to `asyncio.timeout_at`, which requires Python 3.11+. That requirement is referenced here for the minimum Python version.
- The FileLock + `tmp_path_factory` pattern for xdist shared resources is noted in [framework-design.md §3](../framework-design.md) but not adopted in this ADR because xdist is out of scope here.

### Problem Statement

How do we ensure the Harness, all its transports, and every test share a single asyncio event loop for the whole pytest session?

### Goals

- Zero "got Future attached to a different loop" errors from loop-scope mismatch.
- Idiomatic pytest-asyncio usage — no deprecation warnings or legacy overrides.
- Configuration in one place (`pyproject.toml` or `pytest.ini`), not scattered across fixtures.
- Clean path forward as pytest-asyncio evolves.

### Non-Goals

- Multi-process loops (xdist with cross-worker loop sharing). Different architecture.
- Interoperability with sync tests. Sync tests don't touch the loop.
- Custom event loop implementations (uvloop, etc.). Out of scope.

---

## Decision Drivers

- **Correctness** — loop-scope mismatch is a silent source of flakiness and nondeterministic test failures.
- **Forward compatibility** — pytest-asyncio's API has changed across 0.21, 0.23, 0.24. We need something that tracks the current direction.
- **Configuration simplicity** — one setting in `pyproject.toml` beats per-fixture / per-test annotations.
- **Deprecation avoidance** — no legacy `event_loop` fixture overrides, which emit warnings on newer versions.

---

## Considered Options

### Option 1: `loop_scope="session"` via pytest-asyncio 0.24+ (chosen)

**Description:** Use pytest-asyncio ≥ 0.24. Set `asyncio_default_fixture_loop_scope = session` and `asyncio_default_test_loop_scope = session` in `pyproject.toml`. Session-scoped async fixtures declare `@pytest_asyncio.fixture(loop_scope="session", scope="session")`. Tests declare `@pytest.mark.asyncio(loop_scope="session")` (or inherit from defaults).

**Pros:**
- Idiomatic pytest-asyncio 0.24+ pattern.
- Configuration in one place (`pyproject.toml`) means most fixtures and tests need no explicit annotation.
- No deprecation warnings.
- `loop_scope` is documented and stable.

**Cons:**
- Requires pytest-asyncio 0.24 or later — a version floor to enforce.
- All async fixtures that the Harness depends on must also be session-scoped or at least use `loop_scope="session"`. Enforcement is via convention + CI check.

### Option 2: Override the legacy `event_loop` fixture to session scope

**Description:** Provide a custom `event_loop` fixture in `conftest.py` with `scope="session"`.

**Pros:**
- Works on older pytest-asyncio versions (pre-0.24).
- Explicit — the loop-owning fixture is visible.

**Cons:**
- **Deprecated in pytest-asyncio 0.24+.** Emits warnings now; will be removed in a future version.
- Forward-incompatible. We would end up migrating to Option 1 anyway.
- Mixing `event_loop` fixture with new `loop_scope` semantics is a documented source of bugs (pytest-asyncio #944, #950).

### Option 3: Function-scoped loops with manual Future marshalling

**Description:** Each test gets its own loop. When the Harness's Futures (from session loop) are needed in a test (different loop), marshal them via `asyncio.run_coroutine_threadsafe` or similar.

**Pros:**
- Avoids the loop-scope question entirely.

**Cons:**
- Marshalling across loops is a known minefield — Future-compatibility is not guaranteed.
- Defeats the point of a session-scoped Harness: every test still pays the cost of loop setup.
- Not a pattern anyone uses successfully at scale.

### Option 4: Write our own pytest plugin

**Description:** Build a custom plugin that manages the loop explicitly.

**Pros:**
- Full control.

**Cons:**
- Reinvents pytest-asyncio.
- Massive maintenance burden.
- Zero upside over Option 1.

---

## Decision

**Chosen Option:** Option 1 — pytest-asyncio 0.24+ with `loop_scope="session"` configured in `pyproject.toml`.

### Rationale

- It's the current, documented, forward-compatible pattern. No reason to choose anything else unless we have a constraint that blocks 0.24+ (we don't).
- Configuration in `pyproject.toml` means most fixtures and tests need no explicit annotation — the defaults are correct.
- Option 2 is already deprecated; adopting it would be taking on known future migration work.
- Option 3 defeats the architecture.
- Option 4 is disproportionate to the problem.

---

## Consequences

### Positive

- Session-scoped Harness works as intended without "different loop" errors.
- One-line config in `pyproject.toml`; most code needs no annotation.
- Forward-compatible with pytest-asyncio evolution.
- Well-documented pattern; new contributors will recognise it.

### Negative

- Requires **pytest-asyncio ≥ 0.24** as a hard floor. Documented in dev dependencies.
- Requires **pytest ≥ 8.x** (pytest-asyncio 0.24 requires it).
- Every async fixture that interacts with the Harness must use session loop scope (directly or via defaults). A fixture that declares `loop_scope="function"` accidentally will misbehave silently. Mitigation: lint / test that asserts the Harness sees only session-scoped fixtures.

### Neutral

- Python 3.11+ is already required by [framework-design.md §5](../framework-design.md) (`asyncio.timeout_at` for timeout enforcement). This decision doesn't add to the requirement.
- `@pytest.mark.asyncio(loop_scope="session")` on tests is optional if the default in `pyproject.toml` is `session`. Most tests will inherit the default.
- `asyncio_mode = "auto"` (recommended in Implementation) is a trade-off: `async def test_*` functions are auto-treated as `@pytest.mark.asyncio`, removing boilerplate but promoting any `async def` to a test. A sync test accidentally written `async def` gets silently promoted rather than erroring. Accepted as a reasonable default for a codebase that is async by convention.

### Security Considerations

No direct security surface. The session-scoped loop does mean that pending futures from a crashed test can persist into later tests if cleanup is skipped; that exposure is addressed by [ADR-0002](0002-scoped-registry-test-isolation.md) scope cleanup and by redacting `__repr__` requirements tracked in [ADR-0001](0001-single-session-scoped-harness.md) Goals.

---

## Implementation

### `pyproject.toml`

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
asyncio_default_test_loop_scope = "session"
```

(`asyncio_mode = "auto"` means every `async def test_*` is automatically treated as `@pytest.mark.asyncio`. Optional but reduces boilerplate.)

### Harness fixture shape

```
@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def harness():
    b = Harness(...)
    await b.connect()
    yield b
    await b.disconnect()
```

### Dev dependencies

- `pytest >= 8.0`
- `pytest-asyncio >= 0.24`

### Enforcement — concrete

"All async fixtures that the Harness depends on must share session loop scope" is a correctness invariant, not a convention. Enforce it mechanically:

- A `conftest.py` session-start hook walks every discovered `@pytest_asyncio.fixture` and asserts `loop_scope == "session"` for any fixture in the `harness.*` package. Fixtures outside the package (user tests) may use any scope, but the Harness never interacts with them.
- CI log scanner flags any `DeprecationWarning: The event_loop fixture provided by pytest-asyncio has been redefined` or `RuntimeError: got Future attached to a different loop` as a regression against this ADR.
- Upgrade policy for pytest-asyncio: pin to a minor-version range (e.g. `pytest-asyncio >=0.24,<0.26`); new minor versions must pass the suite in a branch before the range is widened.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1: set this up as part of the framework skeleton before any tests are written. One-line config; minimal cost.

---

## Validation

How will we know this decision was correct?

### Success Metrics

- **Zero "got Future attached to a different loop" errors** in CI logs over 90 days.
- **Zero pytest-asyncio deprecation warnings** in CI logs.
- **No regressions** when pytest-asyncio bumps minor versions (0.25, 0.26, etc.). Verified by dependabot + CI.

### Monitoring

- CI log scanner flags any `got Future attached` error as a regression against this ADR.
- CI log scanner flags any pytest-asyncio `DeprecationWarning` as a regression.
- Annual review: is `loop_scope` still the idiomatic pattern, or has pytest-asyncio introduced something newer?

---

## Related Decisions

- [ADR-0001](0001-single-session-scoped-harness.md) — Session-scoped Harness (the consumer of this loop).
- [ADR-0003](0003-threadsafe-call-soon-bridge.md) — Thread-safety bridge (holds a reference to this exact loop for the session's lifetime).
- [ADR-0004](0004-dispatcher-correlation-mediator.md) — Dispatcher (dispatches onto this loop via the bridge).

---

## References

- pytest-asyncio docs: [Concepts — loop scopes](https://pytest-asyncio.readthedocs.io/en/stable/concepts.html)
- pytest-asyncio issue #944: [Session-scoped event loop with async fixtures](https://github.com/pytest-dev/pytest-asyncio/issues/944)
- pytest-asyncio issue #950 — related mixed-scope bug
- [framework-design.md §3](../framework-design.md) — Connection reuse and loop-scope notes
- [PRD-001](../prd/PRD-001-framework-foundations.md) — Framework foundations PRD

---

## Notes

This ADR is small but load-bearing. Every async fixture in the harness must respect session loop scope. A single misconfigured fixture can corrupt the whole suite. A CI check that asserts "all async fixtures used by the Harness have session loop scope" is strongly recommended; the lint rule is short enough to add as part of the skeleton.

**Last Updated:** 2026-04-17

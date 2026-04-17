# 0003. Thread-Safety Bridge via `loop.call_soon_threadsafe`

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-001 — Framework foundations](../prd/PRD-001-framework-foundations.md) — thread-safety requirements

---

## Context

The harness is async (asyncio) but the underlying transports are not: the Ultra Messaging Python SDK fires callbacks on its own context thread, and many other C-extension or session-based SDKs do the same. These callback threads are **not** the asyncio event loop thread.

Resolving an `asyncio.Future` or calling any `loop.*` method from a non-loop thread is unsafe. The common failure mode is `RuntimeError: got Future attached to a different loop` or silent corruption of the loop's internal state. In async integration-test frameworks this is the single most common class of nondeterministic failure.

We need a mechanism that guarantees every piece of loop-touching work runs on the loop thread, regardless of which transport thread generated it.

### Background

- [context.md](../context.md) states the rule: *"The LBM context thread is separate from the asyncio event loop. Callbacks from LBM must use `loop.call_soon_threadsafe()` to resolve futures safely."*
- [framework-design.md](../framework-design.md) reiterates this for any non-async-native transport.
- Python's `asyncio` docs call `loop.call_soon_threadsafe` out as *the* way to schedule work from a non-loop thread. All other loop APIs are thread-unsafe unless explicitly documented otherwise.

### Problem Statement

How does the framework safely hand off work from a non-async transport's callback thread to the asyncio event loop where Futures can be resolved and async code can run?

### Goals

- Zero `"Future attached to different loop"` errors.
- Zero race conditions visible to test authors.
- Idiomatic Python — use stdlib primitives where they exist.
- Bounded latency between transport callback and loop-side future resolution.

### Non-Goals

- Cross-process communication (xdist). Different problem.
- Non-asyncio concurrency (trio, threading without asyncio). Out of scope.
- Message queuing with backpressure. Transport already queues at its layer.

---

## Decision Drivers

- **Correctness** — the most common async integration-test bug class. Getting this wrong makes the whole suite flaky.
- **Simplicity** — fewer moving parts means less to go wrong.
- **Stdlib-first** — avoid dependencies where Python already provides a solution.
- **Performance** — the bridge is on the hot path for every inbound message.
- **Visibility** — when the bridge fails, the failure must be loud, not silent.

---

## Considered Options

### Option 1: `loop.call_soon_threadsafe` in one central helper

**Description:** Every inbound callback from a transport thread calls a single helper (e.g. `poster.post(callable, *args)`) that wraps `loop.call_soon_threadsafe(callable, *args)`. No other loop access from non-loop threads anywhere.

**Pros:**
- Python's documented mechanism for exactly this scenario (stdlib, stable since 3.4).
- O(1) per call; no queue management, no locks.
- One helper function with one method to review, test, and enforce via CI check.
- Failure modes (loop closed, callback raises) are documented by CPython upstream.
- Works natively with `asyncio.Future.set_result`, `set_exception`, event firing.

**Cons:**
- Callers must always use the helper. One forgotten direct call re-introduces the bug. Enforced by an AST-based CI check (see Implementation) that rejects any transport-module code referencing `loop.call_soon`, `loop.call_later`, or `loop.run_*` outside the bridge.

### Option 2: Async queue with a dedicated dispatcher task

**Description:** Transport callbacks push messages onto a `queue.Queue` (thread-safe). A dedicated asyncio task pulls from the queue and dispatches on the loop thread.

**Pros:**
- Natural backpressure if the consumer is slow.
- Clean separation between producer (transport thread) and consumer (loop task).
- Easier to introspect the queue for debugging.

**Cons:**
- More moving parts: queue + dispatcher task + lifecycle management.
- Two threads synchronising via a queue adds per-message overhead beyond `call_soon_threadsafe`.
- Still needs `call_soon_threadsafe` or similar if the consumer ever needs to wake the loop (e.g. empty queue → new arrival).
- Backpressure may be a disadvantage: we want inbound messages to reach Futures with minimal delay.

### Option 3: Lock-based synchronisation on shared state

**Description:** Callbacks acquire a `threading.Lock` before touching any shared state (including the loop's internals).

**Pros:**
- Minimal new code to write initially (a lock and a contextmanager).

**Cons:**
- Fundamentally wrong. `asyncio.Future` and `asyncio.Loop` are not designed for multi-thread access even with locks. Python docs are explicit.
- Would deadlock or corrupt state in subtle ways.
- Ruled out on correctness grounds.

### Option 4: Run transports inside the loop via `run_in_executor`

**Description:** Spawn transport callbacks using `loop.run_in_executor`, so they run inside the loop's thread pool.

**Pros:**
- Might appear to eliminate the threading boundary.

**Cons:**
- Doesn't actually move the transport thread — the underlying SDK's callback threads are owned by those SDKs, not by our executor.
- Misunderstands the problem: the boundary is set by the SDK, not by us.

---

## Decision

**Chosen Option:** Option 1 — `loop.call_soon_threadsafe` in one central bridge helper.

### Rationale

- It's the primitive Python explicitly provides for this scenario. Using anything else would be reinventing it, probably worse.
- O(1) overhead is the best we can get; the hot-path matters for suites of thousands of inbound messages.
- One helper = one place to get right. Framework-internal tests enforce that no transport-thread code touches the loop directly.
- Existing precedent: many asyncio libraries (aiosqlite, databases, etc.) bridge thread boundaries the same way.
- Keeps the design minimal — no extra tasks, queues, or lifecycle to manage.

---

## Consequences

### Positive

- Zero `"Future attached to different loop"` errors, enforced by construction.
- Minimal overhead per inbound message (single function-call indirection over `call_soon_threadsafe`).
- Single point of test: one method (`LoopPoster.post`) with ~5 framework-internal unit tests covers the full surface.
- Single point of audit: a CI AST check that transport modules reference no `loop.*` attribute names is cheap to write and runs on every PR.

### Negative

- Any new code path that resolves Futures from a transport thread must go through the helper. A slip reintroduces the bug. Mitigated by the AST CI check described in Implementation.
- No backpressure or queue introspection. If the underlying transport fires bursts (replay, session recovery, batch delivery) faster than the loop drains, the loop's callback queue grows unboundedly. Mitigated by exposing queue-depth telemetry (see Monitoring); revisit if measured under real load.

### Neutral

- The helper's signature is bounded: see Implementation for the enumerated set of accepted callables. Not arbitrary.
- Per-message latency depends on the loop's `call_soon` execution order. Under normal load it's microseconds. Under high load, it's bounded by the loop's ability to drain its callback queue.

### Security Considerations

`LoopPoster.post` accepts a callable from a transport thread. If any transport code path constructs the callable from payload fields (e.g. a dispatch table keyed by `msg.type`), a malformed inbound could select an unintended callable. Constrain `post` to a bounded set of internal method references — `Dispatcher.dispatch`, `asyncio.Future.set_result`, `asyncio.Future.set_exception`, `asyncio.Event.set`. The CI check (Implementation) verifies that no transport module passes a payload-derived callable to `post`.

---

## Implementation

1. Create `LoopPoster` as a small helper class holding a reference to the loop.
2. Expose one method: `post(callable, *args)` which calls `loop.call_soon_threadsafe(callable, *args)`.
3. `post` performs an identity check against a whitelist of allowed callables (`Dispatcher.dispatch`, `asyncio.Future.set_result`, `asyncio.Future.set_exception`, `asyncio.Event.set`) in debug builds. In release builds the check is skipped for performance; the AST CI check below catches misuse statically.
4. Every transport owns a reference to the LoopPoster and uses it in its callbacks. Nowhere else in the transport module may touch the loop.
5. **AST-based CI check** (new file `scripts/check_loop_access.py`, run in CI): walks the `harness.transport.*` package modules and asserts no `Attribute(value=Name('loop' or '_loop'), attr=X)` where `X in {'call_soon', 'call_later', 'run_until_complete', 'run_forever', 'stop', 'close'}` outside `loop_bridge.py`. Also asserts that every call to `poster.post` passes a callable that is either a method reference on the whitelist or a `functools.partial` thereof.
6. **Shutdown drain, owned by `Harness.disconnect`:** before closing the loop, `Harness.disconnect` calls `poster.drain(timeout=X)` which blocks until the callback queue is empty or the timeout expires, then logs any remaining queue depth. The bridge does NOT swallow-and-log on closed loop; instead, `Harness.disconnect` is the only place the loop can be closed, and it does so after the drain.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (mock-backed skeleton, no SDK): bridge works trivially because `MockLbmTransport` doesn't have threads.
- Phase 2 (real UM SDK): first real exercise of the bridge. Stress-tested with a high-volume callback generator.

---

## Validation

How will we know this decision was correct?

### Success Metrics

- **Zero "Future attached to different loop" errors** in CI logs for 90 days after go-live.
- **Latency** between transport callback and Future resolution **under 1ms p99** on commodity hardware. Measured via framework instrumentation.
- **Zero reports** of "spurious shutdown error" or similar from tests terminating while callbacks are in flight.

### Monitoring

- CI log scanner flags any `RuntimeError` from asyncio loop access as a regression.
- Framework metric: counter of bridge posts per second, histogram of queue-to-resolution latency.
- Framework metric: **callback-queue depth gauge**, sampled every second. Spikes indicate the loop is falling behind the transport's producer rate.
- `scripts/check_loop_access.py` runs in CI on every PR; failure blocks merge.

---

## Related Decisions

- [ADR-0001](0001-single-session-scoped-harness.md) — Single Harness with transport ownership (this bridge lives inside the Harness).
- [ADR-0005](0005-pytest-asyncio-session-loop.md) — Session-scoped event loop (the bridge holds a reference to exactly this loop for the life of the session).
- [PRD-001](../prd/PRD-001-framework-foundations.md) — Framework foundations PRD.

---

## References

- Python docs: [`asyncio.loop.call_soon_threadsafe`](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.call_soon_threadsafe)
- Python docs: [Concurrency and multithreading in asyncio](https://docs.python.org/3/library/asyncio-dev.html#concurrency-and-multithreading)
- [context.md §14](../context.md) — Existing guidance on thread safety
- [framework-design.md §10](../framework-design.md) — Cross-cutting patterns including thread safety

---

## Notes

The bridge is small code (around 20 lines). What matters is the **AST CI check** in `scripts/check_loop_access.py` — without it, the bridge is one careless PR away from being bypassed. Treat the check as part of the ADR's enforcement, not an optional extra.

**Owner for the AST check and shutdown drain specification:** Platform / Test Infrastructure.

**Last Updated:** 2026-04-17

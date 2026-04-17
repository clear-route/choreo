# 01 — Hello, Choreo

The smallest useful test. Publishes a dict, expects a matching shape on the
same topic, asserts passed. No broker, no containers — `MockTransport`
handles everything in memory.

## Run it

```bash
pytest examples/01-hello-world/
```

## What's going on

A Choreo test has four moving parts:

1. **Expectation.** `s.expect(topic, matcher)` declares "a message on this
   topic should match this shape." Returns a `Handle` you can inspect later.
2. **Publish.** `s.publish(topic, payload)` sends a message through the
   transport. Here we publish the same event the SUT would produce; in a real
   test this would be the side-effect of calling your service.
3. **Await.** `s.await_all(timeout_ms=...)` waits for every handle to resolve
   or the deadline to fire. Every expectation gets the same deadline.
4. **Assert.** `result.assert_passed()` raises `AssertionError` with a
   breakdown of every non-passing handle. The diagnostic distinguishes a
   silent timeout (routing bug) from a near-miss (expectation bug).

## Matchers used here

- `contains_fields({...})` — recursive subset match on dicts and lists.
- `gt(0)` — a leaf matcher, composable inside `contains_fields`.

See [docs/guides/matchers.md](../../docs/guides/matchers.md) for the full
cookbook.

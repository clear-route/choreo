# 02 — Request / reply

When your system under test talks to an upstream service, you don't want
to stand that upstream up for every test. Choreo's `on(trigger).publish(reply)`
primitive lets you stage a fake upstream *inside* the test: when a message
arrives on the trigger topic, the framework synthesises a reply.

## Run it

```bash
pytest examples/02-request-reply/
```

## What's going on

Two tests in this example.

**Happy path** — the SUT publishes `payments.request`, the staged upstream
replies on `payments.response` with a matching `request_id`, the scenario
expects the response.

**Non-matching trigger** — the reply has an optional matcher. A trigger
message that fails the matcher counts as a *candidate* but doesn't fire
the reply. `result.reply_at(...)` returns a `ReplyReport` with four states
that tell you exactly what happened:

| State | Meaning |
|---|---|
| `REPLIED` | matcher passed, reply went out |
| `ARMED_MATCHER_REJECTED` | trigger messages arrived, matcher rejected them |
| `ARMED_NO_MATCH` | no trigger messages arrived at all |
| `REPLY_FAILED` | builder raised or publish refused |

## Rules

- Register `on(...)` **before** `publish()` — replies are pre-trigger
  arrangements, not background subscriptions.
- A chain fires **once per scope**. Calling `.publish(...)` twice on the
  same `ReplyChain` raises `ReplyAlreadyBoundError`.
- The payload can be a static `dict` / `bytes` or a callable
  `Callable[[decoded_trigger], dict | bytes]`.
- Builder exceptions are captured as the exception **class name only**;
  the report never interpolates `str(e)` (which could contain PII from a
  failing builder).

See ADR-0016 / ADR-0017 / ADR-0018 for the design.

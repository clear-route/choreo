"""02 — Request / reply.

Your system under test talks to an upstream service that you don't want to
stand up for every test. Choreo's `on(trigger).publish(reply)` primitive
lets you stage a fake upstream *inside* the test: when a message arrives on
the trigger topic, the framework synthesises a reply.

Scenario: the SUT submits a request and should react to whatever the
upstream responds with. Here we stand in for the upstream.

Run it:

    pytest examples/02-request-reply/
"""

from pathlib import Path

from choreo import Harness
from choreo.matchers import contains_fields, field_equals
from choreo.transports import MockTransport

ALLOWLIST = Path(__file__).parent / "allowlist.yaml"


async def test_the_upstream_service_should_respond_to_requests_with_a_completed_status() -> None:
    transport = MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost")
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("request-reply") as s:
            # Observe the upstream's reply — what the SUT reacts to.
            h = s.expect(
                "payments.response",
                contains_fields({"request_id": "REQ-1", "status": "COMPLETED"}),
            )

            # Stand in for the upstream service. Whenever
            # `payments.request` arrives, synthesise a matching response.
            #
            # The builder receives the decoded trigger payload; return a
            # dict and the framework publishes it on `payments.response`.
            s.on("payments.request", field_equals("kind", "CHARGE")).publish(
                "payments.response",
                lambda request: {
                    "request_id": request["request_id"],
                    "status": "COMPLETED",
                    "amount": request["amount"],
                },
            )

            # Act as the SUT: send the request that would normally originate
            # from your service under test.
            s = s.publish(
                "payments.request",
                {"request_id": "REQ-1", "kind": "CHARGE", "amount": 4200},
            )

            result = await s.await_all(timeout_ms=500)

        result.assert_passed()

        # The expect handle surfaces what the reply actually looked like.
        assert h.was_fulfilled()
        assert h.message["amount"] == 4200

        # The reply itself has a lifecycle report — useful when the reply
        # never fires and you need to know why.
        report = result.reply_at("payments.request")
        assert report.state.name == "REPLIED"
        assert report.match_count == 1


    finally:
        await harness.disconnect()


async def test_a_non_matching_trigger_should_not_fire_the_reply() -> None:
    """The reply has an optional matcher. Messages on the trigger topic that
    don't match are counted as candidates but don't fire the reply — the
    report records this so you can tell "matcher rejected it" from "it
    never arrived"."""
    transport = MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost")
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("reply-with-filter") as s:
            s.on("payments.request", field_equals("kind", "CHARGE")).publish(
                "payments.response",
                {"status": "COMPLETED"},
            )

            # This is the "wrong" kind — the reply's matcher rejects it.
            s = s.publish("payments.request", {"kind": "REFUND"})

            result = await s.await_all(timeout_ms=50)

        # The scenario passes because nothing was expected to happen — but
        # the reply report tells us the matcher mismatched the candidate.
        report = result.reply_at("payments.request")
        assert report.state.name == "ARMED_MATCHER_MISMATCHED"
        assert report.candidate_count == 1
        assert report.match_count == 0
    finally:
        await harness.disconnect()

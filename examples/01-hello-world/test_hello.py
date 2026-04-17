"""01 — Hello, Choreo.

The smallest useful test. We publish a dict, expect a matching shape on the
same topic, and assert the scenario passed. No broker, no containers —
`MockTransport` handles everything in memory.

Run it:

    pytest examples/01-hello-world/
"""

from pathlib import Path

from choreo import Harness
from choreo.matchers import contains_fields, gt
from choreo.transports import MockTransport

ALLOWLIST = Path(__file__).parent / "allowlist.yaml"


async def test_publishing_an_event_should_be_observable_on_the_same_topic() -> None:
    transport = MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost")
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("hello") as s:
            # 1. Declare what we expect to see.
            h = s.expect(
                "events.processed",
                contains_fields({"status": "PASS", "event": {"count": gt(0)}}),
            )

            # 2. Produce it. In a real test this would be the side-effect of
            #    calling the system under test; here we publish directly to
            #    show the round trip.
            s = s.publish(
                "events.processed",
                {"status": "PASS", "event": {"item_id": "ITEM-42", "count": 1000}},
            )

            # 3. Wait for every registered handle to resolve (or the deadline).
            result = await s.await_all(timeout_ms=100)

        # 4. One assertion covers every handle in the scenario. The message is
        #    chosen to distinguish "nothing arrived" from "arrived but wrong
        #    shape" — see `result.failure_summary()` on failure.
        result.assert_passed()

        # The handle exposes the decoded message and the latency from
        # registration to match, for post-hoc assertions or reporting.
        assert h.was_fulfilled()
        assert h.message["event"]["item_id"] == "ITEM-42"
    finally:
        await harness.disconnect()

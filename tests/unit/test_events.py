import asyncio

from enjrorun.context import AppContext
from enjrorun.events import EventBus


async def test_multiple_queue_subscribers_receive_event():
    bus = EventBus()
    a = bus.subscribe({"model.state"})
    b = bus.subscribe({"model.state", "model.log"})
    bus.publish("model.state", {"id": "m", "state": "ready"})
    ea = await asyncio.wait_for(a.get(), 1)
    eb = await asyncio.wait_for(b.get(), 1)
    assert ea.topic == eb.topic == "model.state"
    assert ea.data == {"id": "m", "state": "ready"}


async def test_topic_filtering():
    bus = EventBus()
    sub = bus.subscribe({"model.log"})
    bus.publish("model.state", {"x": 1})
    bus.publish("model.log", {"x": 2})
    ev = await asyncio.wait_for(sub.get(), 1)
    assert ev.data == {"x": 2}
    assert sub.empty()


async def test_unsubscribe_releases_queue():
    bus = EventBus()
    sub = bus.subscribe({"t"})
    assert bus.subscriber_count() == 1
    sub.close()
    assert bus.subscriber_count() == 0
    bus.publish("t", {})  # no error, nothing delivered
    assert sub.empty()


async def test_slow_subscriber_drops_oldest_not_blocks():
    bus = EventBus(queue_size=2)
    sub = bus.subscribe({"t"})
    for i in range(5):
        bus.publish("t", {"i": i})
    got = [(await sub.get()).data["i"] for _ in range(2)]
    assert got == [3, 4]
    assert sub.dropped == 3


def test_sync_handlers_called_and_errors_isolated():
    bus = EventBus()
    seen = []

    def boom(ev):
        raise RuntimeError("handler bug")

    bus.on("request.completed", boom)
    bus.on("request.completed", lambda ev: seen.append(ev.data))
    bus.publish("request.completed", {"id": 1})
    assert seen == [{"id": 1}]


def test_context_holds_bus():
    ctx = AppContext(bus=EventBus())
    assert ctx.bus is not None

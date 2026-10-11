import sqlite3
import time

import pytest

from enjrorun.events import EventBus
from enjrorun.records import RequestRecord
from enjrorun.requestlog import RequestLog


def rec(**kw) -> RequestRecord:
    base = {
        "model_id": "m1", "alias_used": "m:q4", "endpoint": "chat", "stream": False,
        "client_ip": "127.0.0.1", "key_id": None, "prompt_tokens": 3, "completion_tokens": 4,
        "request_body": {"messages": [{"role": "user", "content": "secret prompt"}]},
        "response_body": {"choices": []},
    }
    base.update(kw)
    return RequestRecord(**base)


@pytest.fixture
def setup(tmp_path):
    bus = EventBus()
    cfg = {"ring": 3, "retention": 30}
    log = RequestLog(tmp_path / "s.db", bus, ring_size=lambda: cfg["ring"],
                     retention_days=lambda: cfg["retention"])
    yield bus, log, cfg, tmp_path / "s.db"
    log.close()


def test_records_from_bus_are_persisted_without_bodies(setup):
    bus, log, _, db = setup
    r = rec()
    bus.publish("request.completed", r)
    rows = log.list()
    assert len(rows) == 1 and rows[0]["id"] == r.id
    assert rows[0]["prompt_tokens"] == 3 and rows[0]["model_id"] == "m1"
    assert "request_body" not in rows[0]
    raw = sqlite3.connect(db).execute("select * from requests").fetchall()
    assert "secret prompt" not in repr(raw)


def test_get_includes_bodies_while_in_ring(setup):
    bus, log, _, _ = setup
    r = rec()
    bus.publish("request.completed", r)
    detail = log.get(r.id)
    assert detail["request_body"]["messages"][0]["content"] == "secret prompt"
    assert detail["bodies_available"] is True


def test_ring_evicts_oldest(setup):
    bus, log, _, _ = setup
    recs = [rec() for _ in range(4)]
    for r in recs:
        bus.publish("request.completed", r)
    assert log.get(recs[0].id)["bodies_available"] is False
    assert log.get(recs[0].id)["request_body"] is None
    assert log.get(recs[-1].id)["bodies_available"] is True


def test_ring_size_zero_keeps_no_bodies(setup):
    bus, log, cfg, _ = setup
    cfg["ring"] = 0
    r = rec()
    bus.publish("request.completed", r)
    assert log.get(r.id)["bodies_available"] is False


def test_list_filters_and_order(setup):
    bus, log, _, _ = setup
    bus.publish("request.completed", rec(model_id="a", ts=time.time() - 10))
    bus.publish("request.completed", rec(model_id="b", status="error", error="boom"))
    bus.publish("request.completed", rec(model_id="a", status="cancelled"))
    assert [r["model_id"] for r in log.list()] == ["a", "b", "a"]  # newest first
    assert len(log.list(model_id="a")) == 2
    assert log.list(status="error")[0]["error"] == "boom"
    assert len(log.list(limit=1)) == 1
    newest = log.list()[0]["ts"]
    assert all(r["ts"] < newest for r in log.list(before=newest))


def test_retention_sweep(setup):
    bus, log, cfg, _ = setup
    bus.publish("request.completed", rec(ts=time.time() - 40 * 86400))
    bus.publish("request.completed", rec())
    cfg["retention"] = 30
    assert log.sweep() == 1
    assert len(log.list()) == 1


def test_get_unknown_returns_none(setup):
    assert setup[1].get("nope") is None


def test_schema_version_and_reopen(tmp_path):
    bus = EventBus()
    a = RequestLog(tmp_path / "x.db", bus, ring_size=lambda: 1, retention_days=lambda: 30)
    bus.publish("request.completed", rec())
    a.close()
    b = RequestLog(tmp_path / "x.db", EventBus(), ring_size=lambda: 1, retention_days=lambda: 30)
    assert len(b.list()) == 1
    assert b.schema_version() == 1
    b.close()

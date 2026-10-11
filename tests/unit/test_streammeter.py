import json
from pathlib import Path

from enjrorun.streammeter import StreamMeter, meter_json

CHUNKS = Path(__file__).resolve().parents[1] / "fixtures" / "chunks"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def feed_in_pieces(meter, data: bytes, clock: Clock, size: int = 7):
    for i in range(0, len(data), size):
        clock.t += 0.01
        meter.feed(data[i : i + size])


def test_stream_with_usage_and_odd_boundaries():
    data = (CHUNKS / "chat_stream_usage.sse").read_bytes()
    clock = Clock()
    m = StreamMeter(clock=clock)
    feed_in_pieces(m, data, clock)
    r = m.result()
    assert r.prompt_tokens == 7
    assert r.completion_tokens == 5
    assert r.finish_reason == "stop"
    assert r.done is True
    assert r.timings["predicted_per_second"] == 100.0
    assert r.tok_per_s == 100.0
    assert r.ttft_ms is not None and r.ttft_ms > 0


def test_ttft_ignores_role_only_first_chunk():
    role = b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":null}}]}\n\n'
    tok = b'data: {"choices":[{"index":0,"delta":{"content":"Hi"}}]}\n\n'
    clock = Clock()
    m = StreamMeter(clock=clock)
    clock.t = 0.5
    m.feed(role)
    clock.t = 1.25
    m.feed(tok)
    assert m.result().ttft_ms == 1250.0


def test_tool_call_only_stream_sets_ttft_and_finish():
    data = (CHUNKS / "chat_stream_tools.sse").read_bytes()
    clock = Clock()
    m = StreamMeter(clock=clock)
    feed_in_pieces(m, data, clock, size=13)
    r = m.result()
    assert r.ttft_ms is not None
    assert r.finish_reason == "tool_calls"
    assert r.completion_tokens is None  # no include_usage in this fixture
    assert r.timings["predicted_n"] == 5
    assert r.tok_per_s == 100.0


def test_error_event_captured():
    m = StreamMeter(clock=Clock())
    m.feed(b'data: {"error":{"message":"boom","type":"server_error"}}\n\n')
    assert m.result().error == "boom"


def test_crlf_and_comments_tolerated():
    m = StreamMeter(clock=Clock())
    m.feed(b': keep-alive\r\n\r\ndata: {"choices":[{"delta":{"content":"x"},"finish_reason":"length"}]}\r\n\r\n')
    m.feed(b"data: [DONE]\r\n\r\n")
    r = m.result()
    assert r.finish_reason == "length" and r.done


def test_incomplete_stream_not_done():
    m = StreamMeter(clock=Clock())
    m.feed(b'data: {"choices":[{"delta":{"content":"x"}}]}\n\ndata: {"choi')
    assert m.result().done is False


def test_tok_per_s_fallback_from_usage():
    clock = Clock()
    m = StreamMeter(clock=clock)
    clock.t = 1.0
    m.feed(b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n')
    clock.t = 3.0
    m.feed(b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":10}}\n\n')
    assert m.result().tok_per_s == 5.0  # 10 tokens over the 2 s after the first token


def test_meter_json_chat_and_embeddings():
    chat = {
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        "timings": {"predicted_per_second": 42.0},
    }
    r = meter_json(json.dumps(chat).encode())
    assert (r.prompt_tokens, r.completion_tokens, r.finish_reason, r.tok_per_s) == (3, 4, "stop", 42.0)
    emb = {"data": [], "usage": {"prompt_tokens": 9, "total_tokens": 9}}
    r = meter_json(json.dumps(emb).encode())
    assert r.prompt_tokens == 9 and r.completion_tokens is None
    assert meter_json(b"not json").error == "unparseable response"

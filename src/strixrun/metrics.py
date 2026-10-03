"""Prometheus text exposition, fed by the event bus. No client library needed."""

from collections import defaultdict

from strixrun.events import Event, EventBus
from strixrun.records import RequestRecord

TTFT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30)
TPS_BUCKETS = (1, 5, 10, 20, 40, 80, 160, 320)


class _Histogram:
    def __init__(self, buckets: tuple) -> None:
        self.buckets = buckets
        self.counts = [0] * len(buckets)
        self.total = 0
        self.sum = 0.0

    def observe(self, value: float) -> None:
        self.total += 1
        self.sum += value
        for i, upper in enumerate(self.buckets):
            if value <= upper:
                self.counts[i] += 1

    def render(self, name: str, labels: str) -> list[str]:
        sep = "," if labels else ""
        lines = [f'{name}_bucket{{{labels}{sep}le="{b:g}"}} {c}' for b, c in zip(self.buckets, self.counts, strict=True)]
        lines.append(f'{name}_bucket{{{labels}{sep}le="+Inf"}} {self.total}')
        lines.append(f"{name}_sum{{{labels}}} {self.sum:g}")
        lines.append(f"{name}_count{{{labels}}} {self.total}")
        return lines


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metrics:
    def __init__(self, bus: EventBus, backend, proxy) -> None:
        self.backend = backend
        self.proxy = proxy
        self.requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self.prompt_tokens: dict[str, int] = defaultdict(int)
        self.completion_tokens: dict[str, int] = defaultdict(int)
        self.ttft: dict[str, _Histogram] = {}
        self.tps: dict[str, _Histogram] = {}
        bus.on("request.completed", self._on_record)

    def _on_record(self, ev: Event) -> None:
        r: RequestRecord = ev.data
        self.requests[(r.model_id, r.endpoint, r.status)] += 1
        self.prompt_tokens[r.model_id] += r.prompt_tokens or 0
        self.completion_tokens[r.model_id] += r.completion_tokens or 0
        if r.ttft_ms is not None:
            self.ttft.setdefault(r.model_id, _Histogram(TTFT_BUCKETS)).observe(r.ttft_ms / 1000)
        if r.tok_per_s:
            self.tps.setdefault(r.model_id, _Histogram(TPS_BUCKETS)).observe(r.tok_per_s)

    def render(self) -> str:
        out = [
            "# HELP strixrun_requests_total Completed requests.",
            "# TYPE strixrun_requests_total counter",
        ]
        for (model, endpoint, status), n in sorted(self.requests.items()):
            out.append(f'strixrun_requests_total{{model="{_esc(model)}",endpoint="{endpoint}",status="{status}"}} {n}')
        for name, data in (("prompt", self.prompt_tokens), ("completion", self.completion_tokens)):
            out += [f"# TYPE strixrun_{name}_tokens_total counter"]
            out += [f'strixrun_{name}_tokens_total{{model="{_esc(m)}"}} {n}' for m, n in sorted(data.items())]
        out += ["# HELP strixrun_ttft_seconds Time to first token.", "# TYPE strixrun_ttft_seconds histogram"]
        for m, h in sorted(self.ttft.items()):
            out += h.render("strixrun_ttft_seconds", f'model="{_esc(m)}"')
        out += ["# HELP strixrun_tokens_per_second Generation speed.", "# TYPE strixrun_tokens_per_second histogram"]
        for m, h in sorted(self.tps.items()):
            out += h.render("strixrun_tokens_per_second", f'model="{_esc(m)}"')
        out += ["# TYPE strixrun_model_loaded gauge", "# TYPE strixrun_queue_depth gauge"]
        for m in sorted(self.backend.loaded()):
            out.append(f'strixrun_model_loaded{{model="{_esc(m)}"}} 1')
            out.append(f'strixrun_queue_depth{{model="{_esc(m)}"}} {self.proxy.queue_depth(m)}')
        return "\n".join(out) + "\n"

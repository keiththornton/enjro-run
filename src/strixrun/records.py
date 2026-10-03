"""RequestRecord: what the Proxy publishes on `request.completed` for every request."""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestRecord:
    model_id: str
    alias_used: str
    endpoint: str  # chat | completions | embeddings | upstream
    stream: bool
    client_ip: str | None
    key_id: str | None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)
    status: str = "ok"  # ok | error | cancelled
    http_status: int | None = None
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    ttft_ms: float | None = None
    total_ms: float | None = None
    tok_per_s: float | None = None
    queue_ms: float | None = None
    request_body: Any = None  # kept only in the in-memory ring, never written to disk
    response_body: Any = None

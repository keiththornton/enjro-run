"""Request history: metadata in SQLite, full bodies only in a small in-memory ring.

Bodies (prompts and completions) are never written to disk. The ring keeps the last
N requests' bodies for the dashboard's detail view and replay. N=0 disables it.
"""

import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from strixrun.events import Event, EventBus
from strixrun.records import RequestRecord

COLUMNS = [
    "id", "ts", "model_id", "alias_used", "endpoint", "stream", "status", "http_status",
    "error", "client_ip", "key_id", "prompt_tokens", "completion_tokens", "ttft_ms",
    "total_ms", "tok_per_s", "queue_ms",
]

MIGRATIONS = [
    """
    CREATE TABLE requests (
        id TEXT PRIMARY KEY,
        ts REAL NOT NULL,
        model_id TEXT NOT NULL,
        alias_used TEXT,
        endpoint TEXT NOT NULL,
        stream INTEGER NOT NULL,
        status TEXT NOT NULL,
        http_status INTEGER,
        error TEXT,
        client_ip TEXT,
        key_id TEXT,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        ttft_ms REAL,
        total_ms REAL,
        tok_per_s REAL,
        queue_ms REAL
    );
    CREATE INDEX requests_ts ON requests (ts DESC);
    CREATE INDEX requests_model_ts ON requests (model_id, ts DESC);
    """,
]


def record_row(record: RequestRecord) -> dict:
    d = asdict(record)
    return {k: d[k] for k in COLUMNS}


class RequestLog:
    def __init__(self, db_path: Path, bus: EventBus, ring_size: Callable[[], int],
                 retention_days: Callable[[], int]) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._ring: OrderedDict[str, tuple] = OrderedDict()
        self._ring_size = ring_size
        self._retention_days = retention_days
        self._migrate()
        bus.on("request.completed", self._on_record)

    def _migrate(self) -> None:
        with self._lock, self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self._db.execute("SELECT version FROM schema_version").fetchone()
            version = row[0] if row else 0
            for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
                self._db.executescript(sql)
                self._db.execute("DELETE FROM schema_version")
                self._db.execute("INSERT INTO schema_version VALUES (?)", (i,))

    def schema_version(self) -> int:
        return self._db.execute("SELECT version FROM schema_version").fetchone()[0]

    def close(self) -> None:
        self._db.close()

    def _on_record(self, ev: Event) -> None:
        record: RequestRecord = ev.data
        row = record_row(record)
        with self._lock, self._db:
            self._db.execute(
                f"INSERT OR REPLACE INTO requests ({', '.join(COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in COLUMNS)})",
                [row[c] for c in COLUMNS],
            )
        size = max(0, self._ring_size())
        if size:
            self._ring[record.id] = (record.request_body, record.response_body)
        while len(self._ring) > size:
            self._ring.popitem(last=False)

    def list(self, limit: int = 100, before: float | None = None, model_id: str | None = None,
             status: str | None = None) -> list[dict]:
        clauses, args = [], []
        if before is not None:
            clauses.append("ts < ?")
            args.append(before)
        if model_id:
            clauses.append("model_id = ?")
            args.append(model_id)
        if status:
            clauses.append("status = ?")
            args.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.execute(
            f"SELECT * FROM requests {where} ORDER BY ts DESC LIMIT ?", [*args, min(limit, 1000)]
        ).fetchall()
        return [_row_dict(r) for r in rows]

    def get(self, request_id: str) -> dict | None:
        row = self._db.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            return None
        d = _row_dict(row)
        bodies = self._ring.get(request_id)
        d["bodies_available"] = bodies is not None
        d["request_body"], d["response_body"] = bodies if bodies else (None, None)
        return d

    def sweep(self) -> int:
        cutoff = time.time() - self._retention_days() * 86400
        with self._lock, self._db:
            return self._db.execute("DELETE FROM requests WHERE ts < ?", (cutoff,)).rowcount


def _row_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["stream"] = bool(d["stream"])
    return d

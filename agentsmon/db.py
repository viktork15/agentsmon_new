"""Tiny SQLite uptime store — powers per-service SLA + uptime + timeline.

One table, ``probes(ts, service, up, latency, detail)``: every probe of a monitored service
appends a row. From that we derive current status, current uptime streak, SLA over a window, and
a bucketed timeline for the dashboard. Standard library only (sqlite3, WAL mode for safe
concurrent read while the probe thread writes).
"""
from __future__ import annotations

import sqlite3
import threading
import time

from . import config

# Schema (table + index + WAL) only needs to be set up once per process. Running
# the DDL on every connection is wasted work since the dashboard opens a fresh
# connection per helper call. ``journal_mode=WAL`` persists in the DB file, so
# later connections inherit it without re-issuing the pragma.
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


class _ClosingConnection(sqlite3.Connection):
    """sqlite3 context manager that also closes the file descriptors on exit.

    The default sqlite3.Connection context manager commits/rolls back but does
    not close the connection. The dashboard calls the DB helpers on every poll,
    so not closing here leaks uptime.sqlite and uptime.sqlite-wal descriptors.
    """

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def _path() -> str:
    return str(config.state_dir() / "uptime.sqlite")


def _conn() -> sqlite3.Connection:
    global _SCHEMA_READY
    c = sqlite3.connect(_path(), timeout=10, factory=_ClosingConnection)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=5000")   # per-connection, must be set every time
    if not _SCHEMA_READY:
        with _SCHEMA_LOCK:
            if not _SCHEMA_READY:           # double-checked: only the first connection sets up schema
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("""CREATE TABLE IF NOT EXISTS probes(
                    ts INTEGER NOT NULL, service TEXT NOT NULL, up INTEGER NOT NULL,
                    latency REAL, detail TEXT)""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_probes_service_ts ON probes(service, ts)")
                _SCHEMA_READY = True
    return c


def record(service: str, up: bool, latency: float | None = None, detail: str = "",
           ts: int | None = None) -> None:
    with _conn() as c:
        c.execute("INSERT INTO probes(ts,service,up,latency,detail) VALUES(?,?,?,?,?)",
                  (int(ts if ts is not None else time.time()), service, 1 if up else 0, latency, detail))


def last(service: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM probes WHERE service=? ORDER BY ts DESC LIMIT 1",
                      (service,)).fetchone()
    return dict(r) if r else None


def sla(service: str, window_seconds: int) -> tuple[float | None, int]:
    """(% of samples that were up in the window, sample count)."""
    since = int(time.time()) - window_seconds
    with _conn() as c:
        rows = c.execute("SELECT up, COUNT(*) n FROM probes WHERE service=? AND ts>=? GROUP BY up",
                         (service, since)).fetchall()
    total = sum(r["n"] for r in rows)
    up = sum(r["n"] for r in rows if r["up"])
    return (100.0 * up / total if total else None), total


def avg_latency(service: str, window_seconds: int) -> float | None:
    """Average health-check latency (seconds) over the window — the card's 'avg latency' metric
    (distinct from the agent row's current latency)."""
    since = int(time.time()) - window_seconds
    with _conn() as c:
        r = c.execute("SELECT AVG(latency) a FROM probes WHERE service=? AND ts>=? "
                      "AND latency IS NOT NULL", (service, since)).fetchone()
    return r["a"] if r and r["a"] is not None else None


def uptime_seconds(service: str, min_outage: int = 3) -> int | None:
    """Seconds in the current up-streak. A *real outage* is ``min_outage`` or more consecutive
    down samples; isolated transient blips (e.g. one slow health check while the process stays up)
    do NOT reset uptime — so this reads as "time since the last real restart/outage", matching how
    a status page reports uptime. Returns 0 if currently down, None if there's no data."""
    now = int(time.time())
    with _conn() as c:
        rows = c.execute("SELECT ts, up FROM probes WHERE service=? ORDER BY ts", (service,)).fetchall()
    if not rows:
        return None
    if not rows[-1]["up"]:
        return 0
    streak_start = int(rows[0]["ts"])
    run = 0
    for r in rows:
        if not r["up"]:
            run += 1
        else:
            if run >= min_outage:           # a real outage just ended → uptime restarts here
                streak_start = int(r["ts"])
            run = 0
    return now - streak_start


def history_seconds(service: str) -> int:
    """How long we've been recording this service (newest − oldest sample)."""
    with _conn() as c:
        r = c.execute("SELECT MIN(ts) a, MAX(ts) b FROM probes WHERE service=?", (service,)).fetchone()
    return int(r["b"] - r["a"]) if r and r["a"] is not None else 0


def timeline(service: str, window_seconds: int, buckets: int) -> list[dict]:
    """Bucket the window into *buckets* slices. Each → {start: epoch, uptime_pct: float|None}
    (percent of up samples in that bucket; None when no data). The UI greens a bucket at ≥99 %."""
    now = int(time.time())
    start = now - window_seconds
    size = max(1, window_seconds // buckets)
    agg = [[0, 0] for _ in range(buckets)]   # [up_samples, total_samples]
    with _conn() as c:
        rows = c.execute("SELECT ts, up FROM probes WHERE service=? AND ts>=? ORDER BY ts",
                         (service, start)).fetchall()
    for r in rows:
        idx = min(buckets - 1, (int(r["ts"]) - start) // size)
        agg[idx][1] += 1
        agg[idx][0] += int(r["up"])
    out = []
    for i, (up, total) in enumerate(agg):
        out.append({"start": start + i * size,
                    "uptime_pct": (round(100.0 * up / total, 2) if total else None)})
    return out


def prune(retention_seconds: int) -> int:
    """Delete probe rows older than *retention_seconds* and return the rows removed.

    Without this the ``probes`` table grows forever (≈1 row/service/probe-interval),
    and the full-table scans in :func:`uptime_seconds` / :func:`history_seconds` get
    slower over time. Keeping a window that comfortably covers the SLA + timeline
    range preserves every metric the dashboard shows. Cheap thanks to the
    ``(service, ts)`` index. Best-effort: never raises."""
    if retention_seconds <= 0:
        return 0
    cutoff = int(time.time()) - retention_seconds
    try:
        with _conn() as c:
            cur = c.execute("DELETE FROM probes WHERE ts < ?", (cutoff,))
            return cur.rowcount or 0
    except Exception:
        return 0

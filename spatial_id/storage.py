"""Transactional generation and immutable history; no external dependencies in SQLite mode."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import uuid

from .core import EPOCH_MS, MAX_TIMESTAMP, SHIFTS, spatial_bits
from .errors import ClockRegressionError, SequenceExhaustedError, ValidationError, WorkerLeaseError


class StorageUnavailable(RuntimeError):
    pass


class Conflict(ValueError):
    pass


class SequenceBusy(Exception):
    pass


def json_text(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Database:
    """Bounded pools with explicitly scoped transactions and synchronous commits."""

    def __init__(self, url: str, size: int = 4):
        self.url = url
        self.pg = url.startswith(("postgres://", "postgresql://"))
        if self.pg:
            from psycopg_pool import ConnectionPool
            self.pool = ConnectionPool(url, min_size=1, max_size=size, timeout=1,
                kwargs={"autocommit": True, "connect_timeout": 2,
                        "options": "-c statement_timeout=2000 -c lock_timeout=500 -c idle_in_transaction_session_timeout=3000 -c synchronous_commit=on"})
        else:
            path = url.removeprefix("sqlite:///")
            if path == ":memory:":
                raise ValueError("Use a temporary file: pooled SQLite does not support :memory:")
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.pool = queue.Queue(maxsize=size)
            for _ in range(size):
                connection = sqlite3.connect(self.path, timeout=0.5, isolation_level=None, check_same_thread=False)
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA foreign_keys=ON")
                self.pool.put(connection)

    @contextmanager
    def connection(self):
        if self.pg:
            with self.pool.connection(timeout=1) as connection:
                yield connection
        else:
            try:
                connection = self.pool.get(timeout=1)
            except queue.Empty as exc:
                raise StorageUnavailable("database connection pool exhausted") from exc
            try:
                yield connection
            finally:
                self.pool.put(connection)

    def execute(self, connection, sql, parameters=()):
        return connection.execute(sql.replace("?", "%s") if self.pg else sql, parameters)

    @contextmanager
    def transaction(self, write=True):
        with self.connection() as connection:
            self.execute(connection, "BEGIN" if self.pg or not write else "BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def now(self, connection):
        if self.pg:
            return self.execute(connection, "SELECT floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint").fetchone()[0]
        return self.execute(connection, "SELECT CAST((julianday('now') - 2440587.5)*86400000 AS INTEGER)").fetchone()[0]

    def close(self):
        if self.pg:
            self.pool.close()
        else:
            while not self.pool.empty():
                self.pool.get_nowait().close()

    def migrate(self):
        with self.transaction() as c:
            if self.pg:
                self.execute(c, "SELECT pg_advisory_xact_lock(7358911)")
            statements = [
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)",
                "CREATE TABLE IF NOT EXISTS allocations (worker_id INTEGER PRIMARY KEY, owner TEXT NOT NULL, fence BIGINT NOT NULL, expires BIGINT NOT NULL, timestamp BIGINT NOT NULL, sequence INTEGER NOT NULL)",
                "CREATE TABLE IF NOT EXISTS requests (request_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, vehicle_id TEXT, captured_at_ms BIGINT NOT NULL, generated_at_ms BIGINT NOT NULL, request_key TEXT NOT NULL REFERENCES requests(request_key), payload TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS vehicle_history ON records(vehicle_id, captured_at_ms, id)",
            ]
            for statement in statements:
                self.execute(c, statement)
            version = self.execute(c, "SELECT max(version) FROM schema_version").fetchone()[0]
            if version is not None and version > 1:
                raise StorageUnavailable("database schema is newer than this application")
            if version is None:
                if not self.pg:
                    tables = {r[0] for r in self.execute(c, "SELECT name FROM sqlite_master WHERE type='table'")}
                    if "worker_leases" in tables:
                        active = self.execute(c, "SELECT count(*) FROM worker_leases WHERE expires_at_ms > ?", (self.now(c),)).fetchone()[0]
                        if active:
                            raise StorageUnavailable("stop legacy generators and wait for their leases before migration")
                    if "worker_state" in tables:
                        for worker, stamp in self.execute(c, "SELECT worker_id,last_timestamp FROM worker_state").fetchall():
                            self.execute(c, "INSERT INTO allocations VALUES (?, '', 0, 0, ?, 1023) ON CONFLICT(worker_id) DO NOTHING", (worker, stamp))
                self.execute(c, "INSERT INTO schema_version VALUES (1)")


class RecordStore:
    def __init__(self, db: Database, worker_id=0, clock=None, ttl_ms=30000):
        if type(worker_id) is not int or not 0 <= worker_id <= 1023:
            raise ValidationError("worker_id must be between 0 and 1023")
        if ttl_ms < 300:
            raise ValidationError("lease TTL must be at least 300 ms")
        self.db, self.worker_id = db, worker_id
        self.clock = clock or (lambda: time.time_ns() // 1000000)
        self.ttl_ms = ttl_ms
        self.owner = uuid.uuid4().hex
        self.fence = None
        self.stop = threading.Event()
        self.heartbeat = None
        self.lost = False
        self.closed = False
        self.retry_count = 0
        self.sequence_waits = 0

    def acquire(self, heartbeat=True):
        with self.db.transaction() as c:
            self.db.execute(c, "INSERT INTO allocations VALUES (?, '', 0, 0, -1, -1) ON CONFLICT(worker_id) DO NOTHING", (self.worker_id,))
            row = self._worker(c)
            now = self.db.now(c)
            if row[2] > now:
                raise WorkerLeaseError("worker already leased")
            self.fence = row[1] + 1
            self.db.execute(c, "UPDATE allocations SET owner=?, fence=?, expires=? WHERE worker_id=?",
                (self.owner, self.fence, now + self.ttl_ms, self.worker_id))
        self.lost = False
        if heartbeat:
            self.heartbeat = threading.Thread(target=self._renew_loop, daemon=True)
            self.heartbeat.start()

    def _worker(self, c):
        return self.db.execute(c, "SELECT owner,fence,expires,timestamp,sequence FROM allocations WHERE worker_id=?" + (" FOR UPDATE" if self.db.pg else ""), (self.worker_id,)).fetchone()

    def _owned(self, c):
        row = self._worker(c)
        if self.stop.is_set() or self.lost or row is None or row[0] != self.owner or row[1] != self.fence or row[2] <= self.db.now(c):
            raise WorkerLeaseError("worker lease lost or expired")
        return row

    def renew(self):
        with self.db.transaction() as c:
            self._owned(c)
            self.db.execute(c, "UPDATE allocations SET expires=? WHERE worker_id=?", (self.db.now(c) + self.ttl_ms, self.worker_id))

    def _renew_loop(self):
        while not self.stop.wait(1 if self.lost else self.ttl_ms / 3000):
            try:
                if self.lost:
                    self.acquire(heartbeat=False)
                else:
                    self.renew()
            except Exception:
                self.lost = True

    def ready(self):
        with self.db.transaction() as c:
            self._owned(c)
        return {"status": "ok", "worker_id": self.worker_id, "database": "postgresql" if self.db.pg else "sqlite", "lease": "active"}

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()
        if self.heartbeat:
            self.heartbeat.join(timeout=4)
        try:
            with self.db.transaction() as c:
                self.db.execute(c, "UPDATE allocations SET expires=0 WHERE worker_id=? AND owner=? AND fence=?", (self.worker_id, self.owner, self.fence))
        finally:
            self.db.close()

    def generate(self, payload, key=None):
        fields = {"latitude", "longitude", "altitude_m", "speed_mps", "vehicle_class", "count", "vehicle_id", "captured_at_ms"}
        if not isinstance(payload, dict) or set(payload) - fields:
            raise ValidationError("expected an object containing only supported fields")
        normalized = dict(payload)
        for name in ("latitude", "longitude", "altitude_m", "speed_mps"):
            if name not in payload or type(payload[name]) not in (int, float):
                raise ValidationError(f"{name} must be a number")
            try:
                normalized[name] = float(payload[name])
            except (ValueError, OverflowError) as exc:
                raise ValidationError(f"{name} must be a finite number") from exc
        normalized.setdefault("vehicle_class", 0)
        normalized.setdefault("count", 1)
        normalized.setdefault("vehicle_id", None)
        normalized.setdefault("captured_at_ms", None)
        count = normalized["count"]
        if type(count) is not int or not 1 <= count <= 1000:
            raise ValidationError("count must be between 1 and 1000")
        vehicle = normalized["vehicle_id"]
        if vehicle is not None and (not isinstance(vehicle, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", vehicle)):
            raise ValidationError("vehicle_id must contain 1 to 128 letters, digits, dots, underscores, colons or hyphens")
        captured = normalized["captured_at_ms"]
        if captured is not None and (type(captured) is not int or not 0 <= captured <= 253402300799999):
            raise ValidationError("captured_at_ms must be a valid nonnegative millisecond timestamp")
        bits = spatial_bits(*(normalized[n] for n in ("latitude", "longitude", "altitude_m", "speed_mps", "vehicle_class")))
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 128 or not key.isascii()):
            raise ValidationError("Idempotency-Key must contain 1 to 128 ASCII characters")
        key = key or uuid.uuid4().hex
        fingerprint = hashlib.sha256(json_text(normalized).encode()).hexdigest()
        captured = captured if captured is not None else time.time_ns() // 1000000
        deadline = time.monotonic() + 4.0
        saturation_deadline = time.monotonic() + .1
        attempts = 0
        while True:
            try:
                return self._commit(normalized, bits, key, fingerprint, captured)
            except SequenceBusy:
                self.sequence_waits += 1
                if time.monotonic() >= saturation_deadline:
                    raise SequenceExhaustedError("sequence capacity did not become available within 100 ms")
                time.sleep(.001)
            except (ValidationError, Conflict, ClockRegressionError, SequenceExhaustedError, WorkerLeaseError):
                raise
            except Exception as exc:
                attempts += 1
                if attempts >= 3 or time.monotonic() >= deadline or not self._transient(exc):
                    raise StorageUnavailable("storage unavailable; retry with the same Idempotency-Key") from exc
                self.retry_count += 1
                time.sleep(.02 * attempts)

    @staticmethod
    def _transient(exc):
        return (isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()) or getattr(exc, "sqlstate", None) in ("40001", "40P01", "55P03") or type(exc).__name__ in ("OperationalError", "PoolTimeout") and not isinstance(exc, sqlite3.Error)

    def _commit(self, payload, bits, key, fingerprint, captured):
        with self.db.transaction() as c:
            if self.db.pg:
                lock_key = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
                self.db.execute(c, "SELECT pg_advisory_xact_lock(?)", (lock_key,))
            previous = self.db.execute(c, "SELECT fingerprint,response FROM requests WHERE request_key=?", (key,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise Conflict("Idempotency-Key already used for a different payload")
                return json.loads(previous[1]), True
            row = self._owned(c)
            timestamp = self.clock() - EPOCH_MS
            if type(timestamp) is not int or not 0 <= timestamp <= MAX_TIMESTAMP:
                raise ValidationError("generation clock is outside the supported epoch")
            if timestamp < row[3]:
                raise ClockRegressionError("clock moved behind persisted generation time")
            start = row[4] + 1 if timestamp == row[3] else 0
            count = payload["count"]
            if start + count > 1024:
                if timestamp == MAX_TIMESTAMP:
                    raise SequenceExhaustedError("final representable millisecond exhausted")
                raise SequenceBusy()
            prefix = bits | (timestamp << SHIFTS["timestamp"]) | (self.worker_id << SHIFTS["worker"])
            ids = [f"{prefix | seq:032x}" for seq in range(start, start + count)]
            response = {"ids": ids, "count": count, "worker_id": self.worker_id, "request_key": key}
            self.db.execute(c, "INSERT INTO requests VALUES (?, ?, ?)", (key, fingerprint, json_text(response)))
            record = {k: v for k, v in payload.items() if k != "count"}
            record.update(captured_at_ms=captured, generated_at_ms=timestamp + EPOCH_MS, worker_id=self.worker_id, request_key=key)
            rows = [(value, payload["vehicle_id"], captured, timestamp + EPOCH_MS, key, json_text(dict(record, id=value))) for value in ids]
            sql = "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)"
            with c.cursor() if self.db.pg else _sqlite_cursor(c) as cursor:
                cursor.executemany(sql.replace("?", "%s") if self.db.pg else sql, rows)
            self.db.execute(c, "UPDATE allocations SET timestamp=?, sequence=? WHERE worker_id=?", (timestamp, start + count - 1, self.worker_id))
            # Verify lease time again after writes while holding the same worker lock.
            self._owned(c)
            return response, False

    def record(self, value):
        with self.db.connection() as c:
            row = self.db.execute(c, "SELECT payload FROM records WHERE id=?", (value,)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self, vehicle, limit=100, cursor=None, start=None, end=None, latest=False):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValidationError("limit must be between 1 and 1000")
        if start is not None and end is not None and start > end:
            raise ValidationError("from_ms must not exceed to_ms")
        clauses, args = ["vehicle_id=?"], [vehicle]
        if start is not None:
            clauses.append("captured_at_ms>=?"); args.append(start)
        if end is not None:
            clauses.append("captured_at_ms<=?"); args.append(end)
        if cursor:
            try:
                stamp, value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if type(stamp) is not int or not isinstance(value, str) or len(value) != 32:
                    raise ValueError()
            except Exception as exc:
                raise ValidationError("invalid history cursor") from exc
            clauses.append("(captured_at_ms>? OR (captured_at_ms=? AND id>?))")
            args.extend((stamp, stamp, value))
        order = "DESC" if latest else "ASC"
        args.append(limit + 1)
        with self.db.connection() as c:
            rows = self.db.execute(c, f"SELECT payload FROM records WHERE {' AND '.join(clauses)} ORDER BY captured_at_ms {order}, id {order} LIMIT ?", args).fetchall()
        items = [json.loads(r[0]) for r in rows[:limit]]
        next_cursor = None
        if len(rows) > limit and items:
            last = items[-1]
            next_cursor = base64.urlsafe_b64encode(json_text([last["captured_at_ms"], last["id"]]).encode()).decode().rstrip("=")
        return {"items": items, "next_cursor": next_cursor}


@contextmanager
def _sqlite_cursor(connection):
    cursor = connection.cursor()
    try:
        yield cursor
    finally:
        cursor.close()

"""Worker leasing and restart-safe timestamp persistence.

SQLite is the single-host reference coordinator. The generator depends only on
two small guard callbacks, so a production deployment can replace this module
with an etcd, Consul, or DynamoDB implementation without changing ID packing.
"""

from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
import sqlite3
import threading
import time
import uuid
from typing import Callable

from .core import Clock, SpatialIdGenerator
from .errors import RestartSafetyError, WorkerLeaseError

UnixClock = Callable[[], int]


def _system_clock_ms() -> int:
    return time.time_ns() // 1_000_000


class SQLiteWorkerCoordinator:
    """Coordinate exclusive worker ownership and durable timestamp claims."""

    def __init__(self, path: str | Path, clock: UnixClock | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _system_clock_ms
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS worker_leases (
                    worker_id INTEGER PRIMARY KEY CHECK (worker_id BETWEEN 0 AND 1023),
                    owner_token TEXT NOT NULL,
                    expires_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker_state (
                    worker_id INTEGER PRIMARY KEY CHECK (worker_id BETWEEN 0 AND 1023),
                    last_timestamp INTEGER NOT NULL
                );
                """
            )

    def acquire(
        self,
        worker_id: int,
        *,
        ttl_ms: int = 30_000,
        start_heartbeat: bool = True,
    ) -> WorkerLease:
        if isinstance(worker_id, bool) or not isinstance(worker_id, int) or not 0 <= worker_id <= 1023:
            raise WorkerLeaseError("worker_id must be an integer from 0 to 1023")
        if isinstance(ttl_ms, bool) or not isinstance(ttl_ms, int) or ttl_ms < 300:
            raise WorkerLeaseError("lease ttl_ms must be an integer of at least 300")

        owner_token = uuid.uuid4().hex
        now = self._clock()
        expires_at = now + ttl_ms
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT owner_token, expires_at_ms FROM worker_leases WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            if current is not None and current[1] > now:
                connection.rollback()
                raise WorkerLeaseError(f"worker {worker_id} is already leased")
            connection.execute(
                """
                INSERT INTO worker_leases(worker_id, owner_token, expires_at_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    owner_token = excluded.owner_token,
                    expires_at_ms = excluded.expires_at_ms
                """,
                (worker_id, owner_token, expires_at),
            )
            connection.commit()

        lease = WorkerLease(self, worker_id, owner_token, ttl_ms, expires_at)
        if start_heartbeat:
            lease.start()
        return lease

    def renew(self, worker_id: int, owner_token: str, ttl_ms: int) -> int:
        now = self._clock()
        expires_at = now + ttl_ms
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE worker_leases SET expires_at_ms = ?
                WHERE worker_id = ? AND owner_token = ? AND expires_at_ms > ?
                """,
                (expires_at, worker_id, owner_token, now),
            )
            if cursor.rowcount != 1:
                raise WorkerLeaseError(f"worker {worker_id} lease was lost before renewal")
        return expires_at

    def claim_timestamp(self, worker_id: int, owner_token: str, timestamp: int) -> None:
        """Persist a millisecond before IDs using it are returned."""

        now = self._clock()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                """
                SELECT 1 FROM worker_leases
                WHERE worker_id = ? AND owner_token = ? AND expires_at_ms > ?
                """,
                (worker_id, owner_token, now),
            ).fetchone()
            if lease is None:
                connection.rollback()
                raise WorkerLeaseError(f"worker {worker_id} lease is not active")

            state = connection.execute(
                "SELECT last_timestamp FROM worker_state WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            if state is not None and timestamp <= state[0]:
                connection.rollback()
                raise RestartSafetyError(
                    f"worker {worker_id} timestamp {timestamp} was already claimed; wait for a later millisecond"
                )
            connection.execute(
                """
                INSERT INTO worker_state(worker_id, last_timestamp) VALUES (?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET last_timestamp = excluded.last_timestamp
                """,
                (worker_id, timestamp),
            )
            connection.commit()

    def release(self, worker_id: int, owner_token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM worker_leases WHERE worker_id = ? AND owner_token = ?",
                (worker_id, owner_token),
            )


class WorkerLease:
    """A renewable, exclusive lease for one worker ID."""

    def __init__(
        self,
        coordinator: SQLiteWorkerCoordinator,
        worker_id: int,
        owner_token: str,
        ttl_ms: int,
        expires_at_ms: int,
    ) -> None:
        self.coordinator = coordinator
        self.worker_id = worker_id
        self.owner_token = owner_token
        self.ttl_ms = ttl_ms
        self._expires_at_ms = expires_at_ms
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._lost_reason: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._heartbeat,
                name=f"spatial-id-worker-{self.worker_id}-lease",
                daemon=True,
            )
            self._thread.start()

    def _heartbeat(self) -> None:
        interval_seconds = self.ttl_ms / 3 / 1000
        while not self._stop.wait(interval_seconds):
            try:
                expires_at = self.coordinator.renew(self.worker_id, self.owner_token, self.ttl_ms)
                with self._lock:
                    self._expires_at_ms = expires_at
            except Exception as exc:  # Safety requires fail-closed behavior.
                with self._lock:
                    self._lost_reason = str(exc)
                self._stop.set()
                return

    def assert_valid(self) -> None:
        with self._lock:
            reason = self._lost_reason
            expires_at = self._expires_at_ms
        if self._stop.is_set() or reason is not None:
            raise WorkerLeaseError(reason or f"worker {self.worker_id} lease is closed")
        if self.coordinator._clock() >= expires_at:
            raise WorkerLeaseError(f"worker {self.worker_id} lease expired")

    def claim_timestamp(self, timestamp: int) -> None:
        self.assert_valid()
        self.coordinator.claim_timestamp(self.worker_id, self.owner_token, timestamp)

    def close(self) -> None:
        if self._stop.is_set() and self._thread is None:
            return
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self.coordinator.release(self.worker_id, self.owner_token)

    def __enter__(self) -> WorkerLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class DurableSpatialIdGenerator:
    """A generator protected by a renewable lease and durable time barrier."""

    def __init__(
        self,
        coordinator: SQLiteWorkerCoordinator,
        worker_id: int,
        *,
        lease_ttl_ms: int = 30_000,
        clock: Clock | None = None,
    ) -> None:
        self.lease = coordinator.acquire(worker_id, ttl_ms=lease_ttl_ms)
        self.generator = SpatialIdGenerator(
            worker_id,
            clock,
            availability_guard=self.lease.assert_valid,
            timestamp_guard=self.lease.claim_timestamp,
        )

    @property
    def worker_id(self) -> int:
        return self.generator.worker_id

    def generate(
        self,
        latitude: float,
        longitude: float,
        altitude_m: float,
        speed_mps: float,
        vehicle_class: int = 0,
    ) -> str:
        return self.generator.generate(latitude, longitude, altitude_m, speed_mps, vehicle_class)

    def close(self) -> None:
        self.lease.close()

    def __enter__(self) -> DurableSpatialIdGenerator:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

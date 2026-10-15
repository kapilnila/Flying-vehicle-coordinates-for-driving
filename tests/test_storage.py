import math
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time

import pytest

from spatial_id.core import EPOCH_MS, SpatialIdGenerator, decode
from spatial_id.errors import ClockRegressionError, SequenceExhaustedError, WorkerLeaseError
from spatial_id.storage import Conflict, Database, RecordStore, StorageUnavailable

PAYLOAD = dict(latitude=12.9716, longitude=77.5946, altitude_m=120.4, speed_mps=17.8, vehicle_id="CAR-01")

@pytest.fixture
def store(tmp_path):
    db = Database(str(tmp_path / "records.db")); db.migrate()
    store = RecordStore(db); store.acquire(heartbeat=False)
    yield store
    store.close()

def test_atomic_history_retry_and_restart(store):
    payload = dict(PAYLOAD, count=12)
    result, replay = store.generate(payload, "retry-me")
    assert not replay
    assert len(set(result["ids"])) == 12
    assert store.generate(payload, "retry-me") == (result, True)
    assert len(store.history("CAR-01")["items"]) == 12
    path = str(store.db.path)
    store.close()
    db = Database(path); db.migrate()
    second = RecordStore(db); second.acquire(heartbeat=False)
    try:
        assert second.generate(payload, "retry-me") == (result, True)
        assert second.record(result["ids"][0])["latitude"] == PAYLOAD["latitude"]
    finally:
        second.close()

def test_conflicting_key(store):
    store.generate(PAYLOAD, "same")
    with pytest.raises(Conflict):
        store.generate(dict(PAYLOAD, speed_mps=5), "same")

def test_concurrent_retries(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.generate(dict(PAYLOAD, count=10), "shared"), range(20)))
    assert sum(not replay for _, replay in results) == 1
    assert len({tuple(r["ids"]) for r, _ in results}) == 1
    assert len(store.history("CAR-01")["items"]) == 10

def test_late_history_and_pagination(store):
    for stamp in [30, 10, 20, 20]:
        store.generate(dict(PAYLOAD, captured_at_ms=stamp))
    first = store.history("CAR-01", 2)
    second = store.history("CAR-01", 2, first["next_cursor"])
    assert [i["captured_at_ms"] for i in first["items"]+second["items"]] == [10, 20, 20, 30]
    assert second["next_cursor"] is None
    assert store.history("CAR-01", 1, latest=True)["items"][0]["captured_at_ms"] == 30

def test_rollback_after_insert_does_not_consume_ids(store, monkeypatch):
    original = store.db.execute
    def fail(c, sql, args=()):
        if sql.startswith("UPDATE allocations SET timestamp"):
            raise sqlite3.OperationalError("database or disk is full")
        return original(c, sql, args)
    monkeypatch.setattr(store.db, "execute", fail)
    with pytest.raises(StorageUnavailable):
        store.generate(PAYLOAD, "disk-full")
    monkeypatch.setattr(store.db, "execute", original)
    assert store.history("CAR-01")["items"] == []
    assert store.generate(PAYLOAD, "disk-full")[1] is False

def test_clock_regression_and_frozen_sequence(store):
    store.clock = lambda: EPOCH_MS + 100
    store.generate(dict(PAYLOAD, count=1000))
    store.generate(dict(PAYLOAD, count=24))
    with pytest.raises(SequenceExhaustedError):
        store.generate(PAYLOAD)
    store.clock = lambda: EPOCH_MS + 99
    with pytest.raises(ClockRegressionError):
        store.generate(PAYLOAD)

def test_stale_fence_and_exclusive_worker(store):
    second = RecordStore(store.db)
    with pytest.raises(WorkerLeaseError):
        second.acquire(heartbeat=False)
    with store.db.transaction() as c:
        store.db.execute(c, "UPDATE allocations SET expires=0")
    second.acquire(heartbeat=False)
    with pytest.raises(WorkerLeaseError):
        store.generate(PAYLOAD)
    assert second.generate(PAYLOAD)[0]["count"] == 1

def test_adjacent_coordinate_boundaries():
    generator = SpatialIdGenerator(0)
    value = generator.generate(math.nextafter(90, -math.inf), math.nextafter(180, -math.inf), 0, 0)
    result = decode(value)
    assert result.latitude.bucket == (1 << 19)-1
    assert result.longitude.bucket == (1 << 20)-1
    result = decode(generator.generate(math.nextafter(0, -math.inf), 0, 0, 0))
    assert result.latitude.bucket == (1 << 18)-1

def test_legacy_migration(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE worker_state(worker_id INTEGER PRIMARY KEY,last_timestamp INTEGER)")
        c.execute("INSERT INTO worker_state VALUES (3, 123)")
    db = Database(str(path)); db.migrate(); db.migrate()
    with db.connection() as c:
        assert c.execute("SELECT timestamp,sequence FROM allocations WHERE worker_id=3").fetchone() == (123,1023)
    db.close()

def test_corrupt_storage_fails_closed(store, monkeypatch):
    original=store.db.execute
    def corrupt(c,sql,args=()):
        if sql.startswith("SELECT fingerprint"):
            raise sqlite3.DatabaseError("database disk image is malformed")
        return original(c,sql,args)
    monkeypatch.setattr(store.db,"execute",corrupt)
    with pytest.raises(StorageUnavailable):store.generate(PAYLOAD,"corruption")
    monkeypatch.setattr(store.db,"execute",original)
    assert store.history("CAR-01")["items"]==[]

def test_sqlite_backup_restores_records(store,tmp_path):
    result,_=store.generate(PAYLOAD,"backup")
    backup=tmp_path/"backup.db"
    with store.db.connection() as source:
        target=sqlite3.connect(backup)
        try:source.backup(target)
        finally:target.close()
    restored=Database(str(backup))
    try:
        # Restore validation is read-only: do not resume allocation from an old backup.
        reader=RecordStore(restored)
        assert reader.record(result["ids"][0])["vehicle_id"]=="CAR-01"
    finally:restored.close()

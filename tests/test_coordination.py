from __future__ import annotations

import pytest

from spatial_id import (
    DurableSpatialIdGenerator,
    EPOCH_MS,
    RestartSafetyError,
    SQLiteWorkerCoordinator,
    WorkerLeaseError,
)


class Clock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def test_worker_lease_is_exclusive_and_reusable(tmp_path) -> None:
    coordinator = SQLiteWorkerCoordinator(tmp_path / "state.db")
    first = coordinator.acquire(12, start_heartbeat=False)
    with pytest.raises(WorkerLeaseError, match="already leased"):
        coordinator.acquire(12, start_heartbeat=False)
    first.close()
    second = coordinator.acquire(12, start_heartbeat=False)
    second.close()


def test_expired_lease_fails_closed(tmp_path) -> None:
    clock = Clock(EPOCH_MS + 100)
    coordinator = SQLiteWorkerCoordinator(tmp_path / "state.db", clock)
    lease = coordinator.acquire(1, ttl_ms=300, start_heartbeat=False)
    clock.value += 300
    with pytest.raises(WorkerLeaseError, match="expired"):
        lease.assert_valid()
    lease.close()


def test_restart_cannot_reuse_a_claimed_millisecond(tmp_path) -> None:
    clock = Clock(EPOCH_MS + 100)
    coordinator = SQLiteWorkerCoordinator(tmp_path / "state.db", clock)
    first = DurableSpatialIdGenerator(coordinator, 5, clock=clock)
    first.generate(0, 0, 0, 0)
    first.close()

    second = DurableSpatialIdGenerator(coordinator, 5, clock=clock)
    with pytest.raises(RestartSafetyError, match="already claimed"):
        second.generate(0, 0, 0, 0)
    clock.value += 1
    assert len(second.generate(0, 0, 0, 0)) == 32
    second.close()


def test_different_workers_may_claim_the_same_time(tmp_path) -> None:
    clock = Clock(EPOCH_MS + 100)
    coordinator = SQLiteWorkerCoordinator(tmp_path / "state.db", clock)
    first = DurableSpatialIdGenerator(coordinator, 1, clock=clock)
    second = DurableSpatialIdGenerator(coordinator, 2, clock=clock)
    one = first.generate(0, 0, 0, 0)
    two = second.generate(0, 0, 0, 0)
    assert one != two
    first.close()
    second.close()


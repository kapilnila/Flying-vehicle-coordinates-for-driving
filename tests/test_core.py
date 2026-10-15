from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from spatial_id import (
    ClockRegressionError,
    EPOCH_MS,
    MalformedIdError,
    SequenceExhaustedError,
    SpatialIdGenerator,
    ValidationError,
    decode,
)
from spatial_id.core import MAX_TIMESTAMP


class Clock:
    def __init__(self, relative_ms: int = 1) -> None:
        self.relative_ms = relative_ms

    def __call__(self) -> int:
        return EPOCH_MS + self.relative_ms


def test_round_trip_and_exact_fields() -> None:
    clock = Clock(12_345)
    generator = SpatialIdGenerator(21, clock)
    value = generator.generate(12.9716, 77.5946, 120.4, 17.8, 3)
    result = decode(value)

    assert len(value) == 32
    assert value == value.lower()
    assert result.hex == value
    assert result.timestamp_ms == EPOCH_MS + 12_345
    assert result.version == 1
    assert result.worker_id == 21
    assert result.vehicle_class == 3
    assert result.sequence == 0
    assert result.altitude_lower_m == 120
    assert result.altitude_upper_m == 121
    assert result.speed_lower_mps == 17
    assert result.latitude.lower <= 12.9716 < result.latitude.upper
    assert result.longitude.lower <= 77.5946 < result.longitude.upper
    assert decode(int(value, 16)) == result


def test_boundaries_are_representable() -> None:
    generator = SpatialIdGenerator(1023, Clock())
    low = decode(generator.generate(-90, -180, -1024, 0, 0))
    high = decode(generator.generate(90, 179.999999, 3071.999, 255.999, 15))

    assert low.latitude.bucket == 0
    assert low.longitude.bucket == 0
    assert low.altitude_bucket == 0
    assert high.latitude.bucket == (1 << 19) - 1
    assert high.longitude.bucket == (1 << 20) - 1
    assert high.altitude_bucket == (1 << 12) - 1
    assert high.speed_bucket == 255


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ((-90.0001, 0, 0, 0, 0), "latitude"),
        ((90.0001, 0, 0, 0, 0), "latitude"),
        ((0, 180, 0, 0, 0), "longitude"),
        ((0, 0, -1024.1, 0, 0), "altitude"),
        ((0, 0, 3072, 0, 0), "altitude"),
        ((0, 0, 0, -0.1, 0), "speed"),
        ((0, 0, 0, 256, 0), "speed"),
        ((0, 0, 0, 0, 16), "vehicle_class"),
        ((float("nan"), 0, 0, 0, 0), "latitude"),
    ],
)
def test_invalid_spatial_inputs(arguments: tuple[float, ...], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        SpatialIdGenerator(0, Clock()).generate(*arguments)


@pytest.mark.parametrize("worker", [-1, 1024, 1.5, True])
def test_invalid_worker_ids(worker: object) -> None:
    with pytest.raises(ValidationError, match="worker_id"):
        SpatialIdGenerator(worker)  # type: ignore[arg-type]


def test_sequence_and_millisecond_rollover() -> None:
    clock = Clock(100)
    generator = SpatialIdGenerator(1, clock)
    first = decode(generator.generate(0, 0, 0, 0))
    second = decode(generator.generate(0, 0, 0, 0))
    clock.relative_ms += 1
    third = decode(generator.generate(0, 0, 0, 0))

    assert (first.sequence, second.sequence, third.sequence) == (0, 1, 0)
    assert first.hex < third.hex


def test_clock_regression_fails_fast() -> None:
    clock = Clock(100)
    generator = SpatialIdGenerator(1, clock)
    generator.generate(0, 0, 0, 0)
    clock.relative_ms = 99
    with pytest.raises(ClockRegressionError):
        generator.generate(0, 0, 0, 0)


def test_sequence_exhaustion_at_final_timestamp() -> None:
    generator = SpatialIdGenerator(0, Clock(MAX_TIMESTAMP))
    for _ in range(1024):
        generator.generate(0, 0, 0, 0)
    with pytest.raises(SequenceExhaustedError):
        generator.generate(0, 0, 0, 0)


@pytest.mark.parametrize(
    "value",
    ["1", "G" * 32, "A" * 32, "0" * 31, -1, 1 << 128, True, None],
)
def test_malformed_ids(value: object) -> None:
    with pytest.raises(MalformedIdError):
        decode(value)  # type: ignore[arg-type]


def test_unsupported_version_is_rejected() -> None:
    generator = SpatialIdGenerator(0, Clock())
    value = int(generator.generate(0, 0, 0, 0), 16)
    value &= ~(15 << 83)
    with pytest.raises(MalformedIdError, match="version"):
        decode(value)


def test_thread_safe_generation_has_no_duplicates() -> None:
    generator = SpatialIdGenerator(7)

    def batch(_: int) -> list[str]:
        return [generator.generate(10, 20, 100, 5, 2) for _ in range(500)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(batch, range(8)))
    values = [value for batch_values in batches for value in batch_values]

    assert len(values) == 4000
    assert len(set(values)) == 4000
    assert {decode(value).worker_id for value in values} == {7}


def test_multiple_workers_do_not_collide() -> None:
    clock = Clock(10)
    generators = [SpatialIdGenerator(worker, clock) for worker in range(10)]
    values = {
        generator.generate(10, 20, 100, 5, 2)
        for generator in generators
        for _ in range(100)
    }
    assert len(values) == 1000


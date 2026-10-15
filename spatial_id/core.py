"""Generation, quantization, packing, and decoding for V1 spatial IDs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
import threading
import time
from typing import Callable

from .errors import (
    ClockRegressionError,
    MalformedIdError,
    SequenceExhaustedError,
    ValidationError,
)

EPOCH_MS = 1_767_225_600_000  # 2026-01-01T00:00:00Z
VERSION = 1
ID_BITS = 128

FIELD_BITS = {
    "timestamp": 41,
    "version": 4,
    "latitude": 19,
    "longitude": 20,
    "altitude": 12,
    "speed": 8,
    "vehicle_class": 4,
    "worker": 10,
    "sequence": 10,
}

SHIFTS = {
    "sequence": 0,
    "worker": 10,
    "vehicle_class": 20,
    "speed": 24,
    "altitude": 32,
    "longitude": 44,
    "latitude": 64,
    "version": 83,
    "timestamp": 87,
}

MAX_TIMESTAMP = (1 << FIELD_BITS["timestamp"]) - 1
MAX_SEQUENCE = (1 << FIELD_BITS["sequence"]) - 1
LATITUDE_CELLS = 1 << FIELD_BITS["latitude"]
LONGITUDE_CELLS = 1 << FIELD_BITS["longitude"]
HEX_PATTERN = re.compile(r"^[0-9a-f]{32}$")

Clock = Callable[[], int]
Guard = Callable[[], None]
TimestampGuard = Callable[[int], None]


@dataclass(frozen=True, slots=True)
class SpatialCell:
    """The bounds and center of a lossy, quantized coordinate cell."""

    bucket: int
    lower: float
    upper: float
    center: float


@dataclass(frozen=True, slots=True)
class DecodedSpatialId:
    """All fields recovered from a V1 spatial ID."""

    value: int
    hex: str
    timestamp_ms: int
    timestamp_utc: datetime
    version: int
    latitude: SpatialCell
    longitude: SpatialCell
    altitude_bucket: int
    altitude_lower_m: int
    altitude_upper_m: int
    speed_bucket: int
    speed_lower_mps: int
    speed_upper_mps: int
    vehicle_class: int
    worker_id: int
    sequence: int

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        return {
            "id": self.hex,
            "value": self.value,
            "timestamp_ms": self.timestamp_ms,
            "timestamp_utc": self.timestamp_utc.isoformat().replace("+00:00", "Z"),
            "version": self.version,
            "latitude": _cell_dict(self.latitude),
            "longitude": _cell_dict(self.longitude),
            "altitude": {
                "bucket": self.altitude_bucket,
                "lower_m": self.altitude_lower_m,
                "upper_m": self.altitude_upper_m,
            },
            "speed": {
                "bucket": self.speed_bucket,
                "lower_mps": self.speed_lower_mps,
                "upper_mps": self.speed_upper_mps,
            },
            "vehicle_class": self.vehicle_class,
            "worker_id": self.worker_id,
            "sequence": self.sequence,
        }


def _cell_dict(cell: SpatialCell) -> dict[str, float | int]:
    return {
        "bucket": cell.bucket,
        "lower": cell.lower,
        "upper": cell.upper,
        "center": cell.center,
    }


def _system_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _finite_number(name: str, value: object) -> float:
    if isinstance(value, bool):
        raise ValidationError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValidationError(f"{name} must be a finite number")
    return number


def _bounded_integer(name: str, value: object, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer from {minimum} to {maximum}")
    if not minimum <= value <= maximum:
        raise ValidationError(f"{name} must be from {minimum} to {maximum}")
    return value


def _quantize_latitude(value: object) -> int:
    latitude = _finite_number("latitude", value)
    if not -90.0 <= latitude <= 90.0:
        raise ValidationError("latitude must be from -90 through 90 degrees")
    if latitude == 90.0:
        return LATITUDE_CELLS - 1
    numerator, denominator = latitude.as_integer_ratio()
    return ((numerator + 90 * denominator) * LATITUDE_CELLS) // (180 * denominator)


def _quantize_longitude(value: object) -> int:
    longitude = _finite_number("longitude", value)
    if not -180.0 <= longitude < 180.0:
        raise ValidationError("longitude must be from -180 inclusive to 180 exclusive")
    numerator, denominator = longitude.as_integer_ratio()
    return ((numerator + 180 * denominator) * LONGITUDE_CELLS) // (360 * denominator)


def _quantize_unit_bucket(name: str, value: object, minimum: int, maximum: int) -> int:
    number = _finite_number(name, value)
    if not minimum <= number < maximum:
        raise ValidationError(f"{name} must be from {minimum} inclusive to {maximum} exclusive")
    return math.floor(number) - minimum


def spatial_bits(latitude: float, longitude: float, altitude_m: float,
                 speed_mps: float, vehicle_class: int = 0) -> int:
    """Validate and pack the invariant part of an ID once for a whole batch."""
    return ((VERSION << SHIFTS["version"])
            | (_quantize_latitude(latitude) << SHIFTS["latitude"])
            | (_quantize_longitude(longitude) << SHIFTS["longitude"])
            | (_quantize_unit_bucket("altitude_m", altitude_m, -1024, 3072) << SHIFTS["altitude"])
            | (_quantize_unit_bucket("speed_mps", speed_mps, 0, 256) << SHIFTS["speed"])
            | (_bounded_integer("vehicle_class", vehicle_class, 0, 15) << SHIFTS["vehicle_class"]))


class SpatialIdGenerator:
    """Thread-safe generator for one exclusively assigned worker ID.

    A custom clock may be supplied for testing. It must return Unix time in
    milliseconds and should advance independently while the generator waits
    for a sequence rollover.
    """

    def __init__(
        self,
        worker_id: int,
        clock: Clock | None = None,
        *,
        availability_guard: Guard | None = None,
        timestamp_guard: TimestampGuard | None = None,
    ) -> None:
        self.worker_id = _bounded_integer("worker_id", worker_id, 0, 1023)
        self._clock = clock or _system_clock_ms
        self._availability_guard = availability_guard
        self._timestamp_guard = timestamp_guard
        self._last_timestamp = -1
        self._sequence = -1
        self._lock = threading.Lock()

    def generate(
        self,
        latitude: float,
        longitude: float,
        altitude_m: float,
        speed_mps: float,
        vehicle_class: int = 0,
    ) -> str:
        """Generate one canonical, lowercase, 32-character hexadecimal ID."""

        bits = spatial_bits(latitude, longitude, altitude_m, speed_mps, vehicle_class)

        with self._lock:
            if self._availability_guard is not None:
                self._availability_guard()
            timestamp, sequence = self._next_timestamp_and_sequence()
            value = (
                (timestamp << SHIFTS["timestamp"])
                | bits
                | (self.worker_id << SHIFTS["worker"])
                | sequence
            )
        return f"{value:032x}"

    def _relative_now(self) -> int:
        raw = self._clock()
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValidationError("clock must return Unix time as integer milliseconds")
        timestamp = raw - EPOCH_MS
        if not 0 <= timestamp <= MAX_TIMESTAMP:
            raise ValidationError("clock timestamp is outside the V1 representable range")
        return timestamp

    def _next_timestamp_and_sequence(self) -> tuple[int, int]:
        timestamp = self._relative_now()
        if timestamp < self._last_timestamp:
            raise ClockRegressionError(
                f"clock moved backward from {self._last_timestamp} to {timestamp} epoch milliseconds"
            )

        if timestamp > self._last_timestamp:
            return self._start_timestamp(timestamp)

        if self._sequence < MAX_SEQUENCE:
            self._sequence += 1
            return timestamp, self._sequence

        if timestamp == MAX_TIMESTAMP:
            raise SequenceExhaustedError("sequence exhausted in the final representable millisecond")

        deadline = time.monotonic() + 0.1
        while timestamp == self._last_timestamp:
            if time.monotonic() >= deadline:
                raise SequenceExhaustedError("clock did not advance within 100 ms")
            if self._availability_guard is not None:
                self._availability_guard()
            time.sleep(0.0001)
            timestamp = self._relative_now()
            if timestamp < self._last_timestamp:
                raise ClockRegressionError(
                    f"clock moved backward from {self._last_timestamp} to {timestamp} epoch milliseconds"
                )

        return self._start_timestamp(timestamp)

    def _start_timestamp(self, timestamp: int) -> tuple[int, int]:
        # A durable service uses this hook to persist the timestamp before the
        # first ID in that millisecond can escape the process.
        if self._timestamp_guard is not None:
            self._timestamp_guard(timestamp)
        self._last_timestamp = timestamp
        self._sequence = 0
        return timestamp, self._sequence


def decode(value: int | str) -> DecodedSpatialId:
    """Decode an integer or canonical hexadecimal V1 spatial ID."""

    id_value = _parse_id(value)
    version = (id_value >> SHIFTS["version"]) & ((1 << FIELD_BITS["version"]) - 1)
    if version != VERSION:
        raise MalformedIdError(f"unsupported spatial ID version {version}")

    timestamp = (id_value >> SHIFTS["timestamp"]) & MAX_TIMESTAMP
    latitude_bucket = (id_value >> SHIFTS["latitude"]) & (LATITUDE_CELLS - 1)
    longitude_bucket = (id_value >> SHIFTS["longitude"]) & (LONGITUDE_CELLS - 1)
    altitude_bucket = (id_value >> SHIFTS["altitude"]) & ((1 << FIELD_BITS["altitude"]) - 1)
    speed_bucket = (id_value >> SHIFTS["speed"]) & ((1 << FIELD_BITS["speed"]) - 1)

    absolute_timestamp = EPOCH_MS + timestamp
    return DecodedSpatialId(
        value=id_value,
        hex=f"{id_value:032x}",
        timestamp_ms=absolute_timestamp,
        timestamp_utc=datetime.fromtimestamp(absolute_timestamp / 1000, tz=timezone.utc),
        version=version,
        latitude=_coordinate_cell(latitude_bucket, LATITUDE_CELLS, -90.0, 180.0),
        longitude=_coordinate_cell(longitude_bucket, LONGITUDE_CELLS, -180.0, 360.0),
        altitude_bucket=altitude_bucket,
        altitude_lower_m=altitude_bucket - 1024,
        altitude_upper_m=altitude_bucket - 1023,
        speed_bucket=speed_bucket,
        speed_lower_mps=speed_bucket,
        speed_upper_mps=speed_bucket + 1,
        vehicle_class=(id_value >> SHIFTS["vehicle_class"]) & 15,
        worker_id=(id_value >> SHIFTS["worker"]) & 1023,
        sequence=id_value & MAX_SEQUENCE,
    )


def _coordinate_cell(bucket: int, count: int, offset: float, span: float) -> SpatialCell:
    width = span / count
    lower = offset + bucket * width
    upper = offset + (bucket + 1) * width
    return SpatialCell(bucket=bucket, lower=lower, upper=upper, center=(lower + upper) / 2)


def _parse_id(value: int | str) -> int:
    if isinstance(value, bool):
        raise MalformedIdError("ID must be a 128-bit integer or canonical 32-character hex string")
    if isinstance(value, int):
        if not 0 <= value < (1 << ID_BITS):
            raise MalformedIdError("integer ID must fit in 128 unsigned bits")
        return value
    if isinstance(value, str) and HEX_PATTERN.fullmatch(value):
        return int(value, 16)
    raise MalformedIdError("ID must be a lowercase, zero-padded, 32-character hex string")

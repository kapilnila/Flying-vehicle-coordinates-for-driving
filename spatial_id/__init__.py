"""Public API for the distributed spatial ID generator."""

from .core import (
    DecodedSpatialId,
    EPOCH_MS,
    FIELD_BITS,
    SHIFTS,
    SpatialCell,
    SpatialIdGenerator,
    VERSION,
    decode,
)
from .coordination import DurableSpatialIdGenerator, SQLiteWorkerCoordinator, WorkerLease
from .errors import (
    ClockRegressionError,
    MalformedIdError,
    RestartSafetyError,
    SequenceExhaustedError,
    SpatialIdError,
    ValidationError,
    WorkerLeaseError,
)

__all__ = [
    "ClockRegressionError",
    "DecodedSpatialId",
    "DurableSpatialIdGenerator",
    "EPOCH_MS",
    "FIELD_BITS",
    "MalformedIdError",
    "RestartSafetyError",
    "SHIFTS",
    "SequenceExhaustedError",
    "SpatialCell",
    "SpatialIdError",
    "SpatialIdGenerator",
    "SQLiteWorkerCoordinator",
    "VERSION",
    "ValidationError",
    "WorkerLeaseError",
    "WorkerLease",
    "decode",
]

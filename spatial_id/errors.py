"""Public exception types for the spatial ID package."""


class SpatialIdError(Exception):
    """Base class for spatial ID failures."""


class ValidationError(SpatialIdError, ValueError):
    """An input cannot be represented by the configured bit layout."""


class ClockRegressionError(SpatialIdError, RuntimeError):
    """The clock moved behind the last timestamp used by this generator."""


class SequenceExhaustedError(SpatialIdError, RuntimeError):
    """No further sequence can be allocated in the representable time range."""


class MalformedIdError(SpatialIdError, ValueError):
    """An ID is not a canonical V1 spatial ID."""


class WorkerLeaseError(SpatialIdError, RuntimeError):
    """A worker ID cannot be acquired or its lease is no longer valid."""


class RestartSafetyError(SpatialIdError, RuntimeError):
    """A worker attempted to reuse a timestamp claimed before a restart."""

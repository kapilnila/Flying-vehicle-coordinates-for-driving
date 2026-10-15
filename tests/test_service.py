from __future__ import annotations

import pytest

from spatial_id import SpatialIdGenerator, decode
from spatial_id.service import ApiError, SpatialIdApplication, create_server


def test_application_generation_decode_health_and_metrics() -> None:
    application = SpatialIdApplication(SpatialIdGenerator(9), max_batch=5)
    generated = application.generate(
        {
            "latitude": 12.9716,
            "longitude": 77.5946,
            "altitude_m": 120.4,
            "speed_mps": 17.8,
            "vehicle_class": 3,
            "count": 2,
        }
    )

    assert generated["worker_id"] == 9
    ids = generated["ids"]
    assert isinstance(ids, list) and len(ids) == 2
    assert decode(ids[0]).worker_id == 9
    assert application.health() == {"status": "ok", "version": 1, "worker_id": 9}
    assert "spatial_id_generated_total 2" in application.metrics.prometheus()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "JSON object"),
        ({}, "missing fields"),
        (
            {"latitude": 0, "longitude": 0, "altitude_m": 0, "speed_mps": 0, "extra": 1},
            "unknown fields",
        ),
        (
            {"latitude": 0, "longitude": 0, "altitude_m": 0, "speed_mps": 0, "count": 6},
            "count",
        ),
    ],
)
def test_application_rejects_invalid_request_shapes(payload: object, message: str) -> None:
    application = SpatialIdApplication(SpatialIdGenerator(0), max_batch=5)
    with pytest.raises(ApiError, match=message):
        application.generate(payload)


def test_server_wires_application_without_external_framework() -> None:
    server = create_server("127.0.0.1", 0, SpatialIdGenerator(3), max_batch=7)
    try:
        assert server.application.max_batch == 7
        assert server.application.generator.worker_id == 3
        assert server.server_address[1] > 0
    finally:
        server.server_close()

import time

import pytest
from fastapi.testclient import TestClient

from spatial_id.api import create_app
from spatial_id.request_log import cutoff_ms


LOG = "/v1/operator/request-logs"
OP = {"Authorization": "Bearer ops-only"}
API = {"Authorization": "Bearer fleet"}
POINT = dict(latitude=12, longitude=23, altitude_m=950, speed_mps=9)


def open_app(path, operator="ops-only"):
    return TestClient(create_app(database_url=str(path), token="fleet", operator_token=operator))


def test_batches_replays_and_late_points_survive_restart(tmp_path):
    path = tmp_path / "service.db"
    with open_app(path) as client:
        body = dict(POINT, count=3)
        headers = {**API, "Idempotency-Key": "private-key"}
        first, replay = [client.post("/v1/ids", json=body, headers=headers) for _ in range(2)]
        assert (first.status_code, replay.status_code) == (201, 200)
        ids = first.json()["ids"]
        attempts = [r.headers["X-Request-ID"] for r in (first, replay)]
        assert attempts[0] != attempts[1]
        for capture in (2000, 1000):
            assert client.post("/v1/ids", json=dict(POINT, vehicle_id="V1", captured_at_ms=capture),
                               headers=API).status_code == 201
    with open_app(path) as client:
        for index, attempt in enumerate(attempts):
            row, = client.get(LOG, params={"request_id": attempt}, headers=OP).json()["items"]
            assert row["replayed"] is bool(index)
            assert row["point_count"] == 3 and row["vehicle_id"] is None
            points = client.get(f"{LOG}/{attempt}/points", headers=OP)
            assert [point["id"] for point in points.json()["items"]] == ids
            assert "private-key" not in points.text
        records = client.get("/v1/vehicles/V1/records", headers=API).json()["items"]
        assert [record["captured_at_ms"] for record in records] == [1000, 2000]
        params = dict(vehicle_id="V1", status=201, limit=1, from_ms=1, to_ms=int(time.time()*1000)+1000)
        page = client.get(LOG, params=params, headers=OP).json()
        older = client.get(LOG, params=dict(params, cursor=page["next_cursor"]), headers=OP).json()
        assert older["next_cursor"] is None
        assert page["items"][0]["request_id"] != older["items"][0]["request_id"]


def test_failures_redaction_exclusions_and_access(tmp_path):
    with open_app(tmp_path / "api.db") as client:
        denied = client.get("/v1/vehicles/V1/records?secret=very-secret")
        invalid = client.post("/v1/ids", json={"vehicle_id": "V1", "secret": "body-secret"}, headers=API)
        for url in ("/livez", "/readyz", "/stats", "/metrics", "/"):
            client.get(url, headers=API)
        assert client.get(LOG, headers=API).status_code == 401
        assert client.get(f"{LOG}/{denied.headers['x-request-id']}/points", headers=API).status_code == 401
        result = client.get(LOG, headers=OP)
        rows = {row["request_id"]: row for row in result.json()["items"]}
        assert len(rows) == 2
        assert rows[denied.headers["x-request-id"]]["route"] == "/v1/vehicles/{vehicle}/records"
        assert rows[denied.headers["x-request-id"]]["status"] == 401
        assert rows[invalid.headers["x-request-id"]]["status"] == 400
        assert all(row["vehicle_id"] == "V1" for row in rows.values())
        for secret in ("very-secret", "body-secret", "Bearer", "ops-only"):
            assert secret not in result.text


@pytest.mark.parametrize("operator", ["", "fleet"])
def test_disabled_operator_access(tmp_path, operator):
    with open_app(tmp_path / "api.db", operator) as client:
        assert client.get(LOG, headers=API).status_code == 403


def test_expiry_and_log_write_failure_preserve_records(tmp_path, monkeypatch, caplog):
    with open_app(tmp_path / "api.db") as client:
        response = client.post("/v1/ids", json=POINT, headers=API)
        attempt = response.headers["x-request-id"]
        log = client.app.state.request_log
        with log.db.transaction() as c:
            log.db.execute(c, "UPDATE request_logs SET started_at_ms=? WHERE request_id=?", (cutoff_ms()-1, attempt))
        assert not client.get(LOG, headers=OP).json()["items"]
        assert client.get(f"{LOG}/{attempt}/points", headers=OP).status_code == 404
        assert log.prune() == 1
        def unavailable(*args):
            raise OSError("sensitive error details")
        monkeypatch.setattr(log, "write", unavailable)
        another = client.post("/v1/ids", json=POINT, headers=API)
        assert another.status_code == 201
        assert another.headers["x-request-id"] in caplog.text
        assert "sensitive error details" not in caplog.text
        for point in [response, another]:
            assert client.get("/v1/records/" + point.json()["ids"][0], headers=API).status_code == 200

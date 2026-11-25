import io
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from scripts import render_keepalive
from spatial_id.api import create_app
from spatial_id.core import EPOCH_MS
from spatial_id.errors import WorkerLeaseError
from spatial_id.storage import Database, RecordStore


def test_auto_workers_overlap_and_preserve_state(tmp_path):
    path = str(tmp_path / "overlap.db")
    first_db = Database(path)
    first_db.migrate()
    first = RecordStore.acquire_available(first_db, heartbeat=False)
    second = RecordStore.acquire_available(Database(path), heartbeat=False)
    payload = dict(latitude=1, longitude=2, altitude_m=3, speed_mps=4, vehicle_id="demo")
    try:
        assert first.worker_id != second.worker_id
        first.clock = second.clock = lambda: EPOCH_MS + 100
        original, _ = first.generate(payload, "original")
        parallel, _ = second.generate(payload, "parallel")
        assert original["ids"] != parallel["ids"]
        worker, fence = first.worker_id, first.fence
        first.close()
        replacement = RecordStore.acquire_available(Database(path), heartbeat=False)
        try:
            replacement.clock = lambda: EPOCH_MS + 100
            assert replacement.worker_id == worker
            assert replacement.fence > fence
            assert replacement.generate(payload, "original") == (original, True)
            fresh, _ = replacement.generate(payload, "fresh")
            assert len(set(original["ids"] + parallel["ids"] + fresh["ids"])) == 3
            assert len(replacement.history("demo")["items"]) == 3
        finally:
            replacement.close()
    finally:
        first.close()
        second.close()


def test_auto_workers_claim_concurrently(tmp_path):
    path = str(tmp_path / "concurrent.db")
    db = Database(path)
    db.migrate()
    db.close()
    with ThreadPoolExecutor(max_workers=4) as executor:
        stores = list(executor.map(lambda _: RecordStore.acquire_available(Database(path)), range(4)))
    try:
        assert len({store.worker_id for store in stores}) == 4
        assert all(store.ready()["lease"] == "active" for store in stores)
    finally:
        for store in stores:
            store.close()


def test_auto_worker_exhaustion_is_bounded(monkeypatch):
    attempts = []
    def occupied(self, heartbeat=True):
        attempts.append(self.worker_id)
        raise WorkerLeaseError("occupied")
    monkeypatch.setattr(RecordStore, "acquire", occupied)
    with pytest.raises(WorkerLeaseError, match="no worker lease"):
        RecordStore.acquire_available(None)
    assert attempts == list(range(1024))


def test_auto_worker_database_error_does_not_try_another_worker(monkeypatch):
    def broken(self, heartbeat=True):
        raise OSError("database disconnected")
    monkeypatch.setattr(RecordStore, "acquire", broken)
    with pytest.raises(OSError, match="disconnected"):
        RecordStore.acquire_available(None)


def test_hosted_assets_are_public_and_records_require_token(tmp_path, monkeypatch):
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "assets").mkdir()
    (frontend / "index.html").write_text("<html>Dashboard</html>")
    (frontend / "assets" / "app.js").write_text("console.log('dashboard')")
    (frontend / "private.txt").write_text("private")
    monkeypatch.setenv("WORKER_ID", "auto")
    with TestClient(create_app(database_url=str(tmp_path / "app.db"), token="private",
                              frontend_dir=frontend)) as client:
        assert client.get("/").status_code == 200
        assert client.head("/").status_code == 200
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/assets/%2e%2e/private.txt").status_code != 200
        assert client.get("/private.txt").status_code == 401
        assert client.get("/livez").json() == {"status": "alive"}
        assert client.get("/readyz").status_code == 200
        payload = dict(latitude=1, longitude=2, altitude_m=3, speed_mps=4, vehicle_id="hosted")
        assert client.post("/v1/ids", json=payload).status_code == 401
        headers = {"Authorization": "Bearer private"}
        assert client.post("/v1/ids", json=payload, headers=headers).status_code == 201
        assert client.get("/v1/vehicles/hosted/records").status_code == 401
        assert len(client.get("/v1/vehicles/hosted/records", headers=headers).json()["items"]) == 1
        assert client.get("/v1/missing", headers=headers).status_code == 404


def test_hosted_dashboard_requires_token(tmp_path):
    with pytest.raises(ValueError, match="API_TOKEN"):
        create_app(frontend_dir=tmp_path, token="")


class HealthResponse(io.BytesIO):
    status = 200


@pytest.mark.parametrize("failures", [0, 1, 2, 3])
def test_keepalive_retries_and_exit_status(monkeypatch, failures):
    calls, delays = [], []
    def request(req, timeout):
        calls.append(req.full_url)
        assert timeout == 60
        if len(calls) <= failures:
            raise URLError("offline")
        return HealthResponse(json.dumps({"status": "alive"}).encode())
    monkeypatch.setenv("RENDER_APP_URL", "https://example.onrender.com/")
    monkeypatch.setattr(render_keepalive.urllib.request, "urlopen", request)
    monkeypatch.setattr(render_keepalive.time, "sleep", delays.append)
    assert render_keepalive.main() == (1 if failures == 3 else 0)
    assert calls == ["https://example.onrender.com/livez"] * min(failures + 1, 3)
    assert delays == [5] * min(failures, 2)


@pytest.mark.parametrize("body,status", [(b"<html>Waking up</html>", 200),
                                         (b'{"status":"starting"}', 200),
                                         (b'{"status":"alive"}', 503)])
def test_keepalive_rejects_bad_responses(monkeypatch, body, status):
    def request(*args, **kwargs):
        response = HealthResponse(body)
        response.status = status
        return response
    monkeypatch.setattr(render_keepalive.urllib.request, "urlopen", request)
    monkeypatch.setattr(render_keepalive.time, "sleep", lambda _: None)
    assert render_keepalive.check("https://example.onrender.com") is False


def test_keepalive_timeout_and_missing_configuration(monkeypatch):
    monkeypatch.delenv("RENDER_APP_URL", raising=False)
    assert render_keepalive.main() == 1
    def timeout(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(render_keepalive.urllib.request, "urlopen", timeout)
    monkeypatch.setattr(render_keepalive.time, "sleep", lambda _: None)
    assert render_keepalive.check("https://example.onrender.com") is False


@pytest.mark.parametrize("url", ["", "file:///etc/passwd", "https://user:secret@example.com",
                                "https://example.com/path", "https://example.com/?token=x"])
def test_keepalive_rejects_invalid_origins(url):
    with pytest.raises(ValueError):
        render_keepalive.health_url(url)

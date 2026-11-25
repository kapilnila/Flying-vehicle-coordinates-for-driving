import pytest
from fastapi.testclient import TestClient

from spatial_id.api import create_app
from spatial_id import hosted


@pytest.mark.parametrize("token", ["", "vehicle-secret"])
def test_public_hosted_access_and_private_operator_logs(tmp_path, monkeypatch, token):
    monkeypatch.setenv("PUBLIC_API", "true")
    frontend = tmp_path / "dashboard"
    frontend.mkdir()
    (frontend / "assets").mkdir()
    (frontend / "index.html").write_text("<html>dashboard</html>")
    with TestClient(create_app(database_url=str(tmp_path / "api.db"), frontend_dir=frontend,
                              token=token, operator_token="operator-secret")) as client:
        assert client.get("/").status_code == 200
        config = client.get("/v1/config")
        assert config.json() == {"public_api": True}
        assert config.headers["cache-control"] == "no-store"
        response = client.post("/v1/ids", json=dict(
            latitude=1, longitude=2, altitude_m=3, speed_mps=4, vehicle_id="public"))
        assert response.status_code == 201
        point = response.json()["ids"][0]
        for path in ("/stats", f"/v1/ids/{point}", f"/v1/records/{point}",
                     "/v1/vehicles/public/records", "/v1/vehicles/public/latest",
                     "/v1/vehicles/public/export", "/v1/vehicles/public/trajectory?window=latest"):
            assert client.get(path).status_code == 200, path
        logs = "/v1/operator/request-logs"
        for headers in ({}, {"Authorization": "Bearer vehicle-secret"}):
            assert client.get(logs, headers=headers).status_code == 401
            assert client.get(f"{logs}/{response.headers['x-request-id']}/points", headers=headers).status_code == 401
        assert client.get(logs, headers={"Authorization": "Bearer operator-secret"}).status_code == 200


def test_disabling_public_access_restores_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_API", "false")
    with TestClient(create_app(database_url=str(tmp_path / "api.db"), token="private")) as client:
        assert client.get("/v1/config").json() == {"public_api": False}
        for path in ("/stats", "/v1/vehicles/public/records"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer private"}).status_code == 200


def test_public_launcher_accepts_missing_token(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/database")
    monkeypatch.setenv("FRONTEND_DIST", "/app/dashboard")
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.setenv("PUBLIC_API", "true")
    calls = []
    monkeypatch.setattr(hosted.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    hosted.main()
    assert calls[0]["host"] == "0.0.0.0"
    monkeypatch.setenv("PUBLIC_API", "false")
    with pytest.raises(SystemExit, match="API_TOKEN"):
        hosted.main()

import pytest
from fastapi.testclient import TestClient
from spatial_id.api import create_app

P = dict(latitude=1, longitude=2, altitude_m=3, speed_mps=4, vehicle_id="demo")

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(database_url=str(tmp_path / "api.db"))) as client:
        yield client

def test_complete_journey(client):
    r = client.post("/v1/ids", json=dict(P, count=2), headers={"Idempotency-Key":"journey"})
    assert r.status_code == 201, r.text
    ids = r.json()["ids"]
    assert isinstance(client.get(f"/v1/ids/{ids[0]}").json()["value"], str)
    assert client.get(f"/v1/records/{ids[0]}").json()["vehicle_id"] == "demo"
    assert len(client.get("/v1/vehicles/demo/records").json()["items"]) == 2
    assert client.get("/v1/vehicles/demo/latest").status_code == 200
    assert len(client.get("/v1/vehicles/demo/export").text.splitlines()) == 2
    retry = client.post("/v1/ids", json=dict(P, count=2), headers={"Idempotency-Key":"journey"})
    assert retry.status_code == 200
    assert retry.json()["ids"] == ids
    assert client.get("/readyz").json()["lease"] == "active"
    assert "spatial_id_generated_total 2" in client.get("/metrics").text

@pytest.mark.parametrize("body", ['{"latitude":NaN}', '{"latitude":1,"latitude":2}', 'invalid'])
def test_strict_json(client, body):
    assert client.post("/v1/ids", content=body, headers={"Content-Type":"application/json"}).status_code == 400

def test_request_limits_and_invalid_fields(client):
    assert client.post("/v1/ids", content="x"*65537).status_code == 413
    assert client.post("/v1/ids", json=dict(P,count=1001)).status_code == 400
    assert client.post("/v1/ids", json=dict(P,latitude="1")).status_code == 400
    assert client.get("/v1/vehicles/demo/records?cursor=bad").status_code == 400

def test_authentication(tmp_path):
    with TestClient(create_app(database_url=str(tmp_path / "auth.db"),token="private")) as client:
        assert client.post("/v1/ids",json=P).status_code == 401
        assert client.post("/v1/ids",json=P,headers={"Authorization":"Bearer private"}).status_code == 201
        assert client.get("/livez").status_code == 200

def test_huge_numbers_and_configured_batch_limit(tmp_path):
    with TestClient(create_app(database_url=str(tmp_path / "limits.db"),max_batch=2)) as client:
        assert client.post("/v1/ids",json=dict(P,latitude=10**400)).status_code == 400
        assert client.post("/v1/ids",json=dict(P,count=3)).status_code == 400
        assert client.post("/v1/ids",json=dict(P,count=2)).status_code == 201

def test_overload_and_unexpected_failure_are_structured(client,monkeypatch):
    client.app.state.inflight=64
    response=client.post("/v1/ids",json=P)
    assert response.status_code==503
    assert response.json()["error"]["code"]=="overloaded"
    client.app.state.inflight=0
    def fail(*args): raise RuntimeError("private diagnostic detail")
    monkeypatch.setattr(client.app.state.store,"generate",fail)
    response=client.post("/v1/ids",json=P)
    assert response.status_code==500
    assert "private diagnostic detail" not in response.text

def test_deadline_retry_resolves_uncertain_commit(client,monkeypatch):
    import threading
    import time
    original=client.app.state.store.generate
    committed=threading.Event()
    def slow(*args):
        time.sleep(5.1)
        result=original(*args)
        committed.set()
        return result
    monkeypatch.setattr(client.app.state.store,"generate",slow)
    response=client.post("/v1/ids",json=P,headers={"Idempotency-Key":"deadline"})
    assert response.status_code==503
    assert response.json()["error"]["code"]=="deadline_exceeded"
    assert committed.wait(2)
    monkeypatch.setattr(client.app.state.store,"generate",original)
    retry=client.post("/v1/ids",json=P,headers={"Idempotency-Key":"deadline"})
    assert retry.status_code==200
    assert len(client.get("/v1/vehicles/demo/records").json()["items"])==1

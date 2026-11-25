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

def test_trajectory_and_astar_route(client):
    points=[(1000,12.9716,77.5946),(11000,12.9720,77.5950),(21000,12.9724,77.5954)]
    ids=[]
    for index,(stamp,lat,lon) in enumerate(points):
        response=client.post("/v1/ids",json=dict(P,latitude=lat,longitude=lon,captured_at_ms=stamp),
                             headers={"Idempotency-Key":f"path-{index}"})
        assert response.status_code==201
        ids.extend(response.json()["ids"])
    trajectory=client.get("/v1/vehicles/demo/trajectory?max_speed_mps=20&max_distance_m=1000").json()
    assert trajectory["summary"]["connected_segment_count"]==2
    assert trajectory["points"][0]["id"]==ids[0]
    truncated=client.get("/v1/vehicles/demo/trajectory?limit=2&max_speed_mps=20&max_distance_m=1000").json()
    assert truncated["summary"]["point_count"]==2
    assert truncated["summary"]["has_more_records"] is True
    assert truncated["summary"]["truncated"] is True
    route=client.get(f"/v1/vehicles/demo/route?start_id={ids[0]}&end_id={ids[-1]}&max_speed_mps=20&max_distance_m=1000")
    assert route.status_code==200
    assert route.json()["route"]["ids"]==ids
    assert client.get("/v1/vehicles/demo/trajectory?limit=1").status_code==400


def test_latest_trajectory_window_and_late_arrivals(client):
    store = client.app.state.store
    for stamp in range(1, 106):
        store.generate(dict(P, captured_at_ms=stamp * 1000))
    url = "/v1/vehicles/demo/trajectory?limit=100"
    historical = client.get(url).json()
    recent = client.get(url + "&window=latest").json()
    assert [p["captured_at_ms"] for p in historical["points"]] == list(range(1000, 101000, 1000))
    assert [p["captured_at_ms"] for p in recent["points"]] == list(range(6000, 106000, 1000))
    assert recent["summary"]["has_more_records"] is True
    newest = recent["points"][-1]["id"]
    store.generate(dict(P, captured_at_ms=103500))
    updated = client.get(url + "&window=latest").json()
    assert updated["points"][-1]["id"] == newest
    assert any(p["captured_at_ms"] == 103500 for p in updated["points"])
    filtered = client.get(url + "&window=latest&from_ms=103000&to_ms=104000").json()
    assert [p["captured_at_ms"] for p in filtered["points"]] == [103000, 103500, 104000]
    assert filtered["summary"]["has_more_records"] is False
    assert client.get(url + "&window=invalid").status_code == 400
    assert client.get("/v1/vehicles/empty/trajectory?window=latest").json()["points"] == []


def test_latest_window_tie_break_and_single_point(client):
    response = client.post("/v1/ids", json=dict(P, captured_at_ms=1000, count=4))
    ids = response.json()["ids"]
    result = client.get("/v1/vehicles/demo/trajectory?window=latest&limit=2").json()
    assert [p["id"] for p in result["points"]] == sorted(ids)[-2:]
    assert result["segments"][0]["reason"] == "non_positive_time"
    client.post("/v1/ids", json=dict(P, vehicle_id="single"))
    assert len(client.get("/v1/vehicles/single/trajectory?window=latest").json()["points"]) == 1


@pytest.mark.parametrize("reference", ["msl", "unknown"])
def test_altitude_reference_in_record_views(client, reference):
    payload = dict(P, altitude_reference=reference)
    headers = {"Idempotency-Key": "reference"}
    response = client.post("/v1/ids", json=payload, headers=headers)
    assert response.status_code == 201
    value = response.json()["ids"][0]
    assert client.post("/v1/ids", json=payload, headers=headers).json() == response.json()
    assert client.get(f"/v1/records/{value}").json()["altitude_reference"] == reference
    assert client.get("/v1/vehicles/demo/latest").json()["altitude_reference"] == reference
    assert client.get("/v1/vehicles/demo/records").json()["items"][0]["altitude_reference"] == reference
    assert client.get("/v1/vehicles/demo/trajectory").json()["points"][0]["altitude_reference"] == reference
    assert f'"altitude_reference":"{reference}"' in client.get("/v1/vehicles/demo/export").text
    other = "unknown" if reference == "msl" else "msl"
    assert client.post("/v1/ids", json=dict(payload, altitude_reference=other), headers=headers).status_code == 409


@pytest.mark.parametrize("reference", [None, True, 42, "gps", "", ["msl"], {"msl": True}])
def test_invalid_altitude_reference(client, reference):
    assert client.post("/v1/ids", json=dict(P, altitude_reference=reference)).status_code == 400

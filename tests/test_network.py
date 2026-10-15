"""Real HTTP tests enabled by SPATIAL_TEST_URL (Compose supplies it)."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest

URL=os.getenv("SPATIAL_TEST_URL")
SECOND=os.getenv("SPATIAL_TEST_SECOND_URL",URL)
pytestmark=pytest.mark.skipif(not URL,reason="requires running HTTP service (Docker integration profile)")

def test_two_replica_history_and_idempotency():
    key=uuid.uuid4().hex
    headers={"Authorization":"Bearer "+os.getenv("API_TOKEN", "")} if os.getenv("API_TOKEN") else {}
    payload=dict(latitude=2,longitude=3,altitude_m=10,speed_mps=2,count=100,vehicle_id=key)
    with httpx.Client(timeout=10,headers=headers) as client:
        def send(i):
            response=client.post((URL if i%2 else SECOND)+"/v1/ids",json=payload,headers={"Idempotency-Key":key})
            assert response.status_code in (200,201),response.text
            return response.json()["ids"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(send,range(16)))
        assert len({tuple(ids) for ids in results})==1
        page=client.get(SECOND+f"/v1/vehicles/{key}/records").json()
        assert len(page["items"])==100
        assert client.get(URL+"/v1/ids/"+results[0][0]).status_code==200
        response=client.post(URL+"/v1/ids",content='{"latitude":NaN}',headers={"Content-Type":"application/json"})
        assert response.status_code==400

def test_live_http_limits_and_disconnection():
    with httpx.Client(timeout=10) as client:
        response=client.post(URL+"/v1/ids",content=b"x"*65537)
        assert response.status_code in (401,413)
        assert client.get(URL+"/livez").status_code==200

def test_client_disconnect_then_retry_is_atomic():
    import json
    import socket
    from urllib.parse import urlsplit
    target=urlsplit(URL)
    if target.scheme!="http": pytest.skip("raw disconnect check requires HTTP test network")
    key=uuid.uuid4().hex
    payload=dict(latitude=2,longitude=3,altitude_m=10,speed_mps=2,count=25,vehicle_id=key)
    body=json.dumps(payload).encode()
    token=os.getenv("API_TOKEN", "")
    authorization=f"Authorization: Bearer {token}\r\n" if token else ""
    head=(f"POST {target.path}/v1/ids HTTP/1.1\r\nHost: {target.netloc}\r\n"
          f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
          f"Idempotency-Key: {key}\r\n{authorization}Connection: close\r\n\r\n").encode()
    with socket.create_connection((target.hostname,target.port or 80),timeout=5) as connection:
        connection.sendall(head+body)
        connection.shutdown(socket.SHUT_WR)
        # Deliberately do not receive the server response.
    with httpx.Client(timeout=10,headers={"Authorization":"Bearer "+token} if token else {}) as client:
        response=client.post(SECOND+"/v1/ids",json=payload,headers={"Idempotency-Key":key})
        assert response.status_code in (200,201),response.text
        records=client.get(SECOND+f"/v1/vehicles/{key}/records").json()["items"]
        assert len(records)==25
        assert {r["id"] for r in records}==set(response.json()["ids"])

"""Host-side destructive scenarios restricted to a dedicated disposable Compose project.

The project name is fixed. Volumes are deliberately retained after the test.
"""
import os
import subprocess
import time
import uuid
import httpx

PROJECT="spatial-id-failure-test"
def compose(*args):
    subprocess.run(["docker","compose","-p",PROJECT,*args],check=True)

def wait_ready(client):
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        try:
            if all(client.get(f"/nodes/{n}/readyz").status_code==200 for n in (0,1)):return
        except httpx.HTTPError: pass
        time.sleep(1)
    raise AssertionError("services did not recover in 60 seconds")

def main():
    os.environ["WEB_PORT"]="18080"
    compose("up","-d","--build","--wait")
    headers={"Authorization":"Bearer "+os.getenv("API_TOKEN", "")} if os.getenv("API_TOKEN") else {}
    try:
        with httpx.Client(base_url="http://127.0.0.1:18080",timeout=8,headers=headers) as client:
            key=uuid.uuid4().hex
            payload=dict(latitude=1,longitude=2,altitude_m=3,speed_mps=4,vehicle_id=key,count=20)
            first=client.post("/v1/ids",json=payload,headers={"Idempotency-Key":key});first.raise_for_status()
            compose("kill","api-0")
            compose("up","-d","api-0")
            wait_ready(client)
            retry=client.post("/v1/ids",json=payload,headers={"Idempotency-Key":key});retry.raise_for_status()
            assert retry.json()["ids"]==first.json()["ids"]
            compose("restart","db")
            wait_ready(client)
            assert len(client.get(f"/v1/vehicles/{key}/records").json()["items"])==20
            print("PASS: API kill/restart, idempotent recovery, database restart, durable history")
    finally:
        compose("down")

if __name__=="__main__": main()

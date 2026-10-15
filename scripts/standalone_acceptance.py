"""Verify persisted SQLite state across an API restart on local test port 18082."""
import os
import subprocess
import time
import uuid
import httpx

def main():
    headers={"Authorization":"Bearer "+os.environ["API_TOKEN"]} if os.getenv("API_TOKEN") else {}
    with httpx.Client(base_url="http://127.0.0.1:18082",timeout=8,headers=headers) as client:
        key=uuid.uuid4().hex
        payload=dict(latitude=1,longitude=2,altitude_m=3,speed_mps=4,vehicle_id=key,count=3)
        original=client.post("/v1/ids",json=payload,headers={"Idempotency-Key":key})
        original.raise_for_status()
        subprocess.run(["docker","compose","-p","spatial-id-sqlite-acceptance","-f","compose.local.yaml","restart","api-0"],
                       env=dict(os.environ,WEB_PORT="18082"),check=True)
        deadline=time.monotonic()+30
        while client.get("/nodes/0/readyz").status_code!=200:
            assert time.monotonic()<deadline
            time.sleep(.5)
        retry=client.post("/v1/ids",json=payload,headers={"Idempotency-Key":key})
        assert retry.status_code==200,retry.text
        assert retry.json()==original.json()
        assert len(client.get(f"/v1/vehicles/{key}/records").json()["items"])==3
        print("PASS: standalone SQLite restart preserves records and retry results")

if __name__=="__main__":main()

"""Verify independent fault cleanup while the runner is paused (acceptance project only)."""
import os
import subprocess
import time
import httpx


def main():
    env = dict(os.environ, WEB_PORT="18081")
    command = ["docker", "compose", "-p", "spatial-id-acceptance", "-f", "compose.yaml", "-f", "compose.test.yaml"]
    def compose(*args):
        subprocess.run(command + list(args), env=env, check=True)
    with httpx.Client(base_url="http://127.0.0.1:18081", timeout=12,
                      headers={"Authorization":"Bearer " + env["TEST_TOKEN"]}) as client:
        response = client.post("/test-api/runs",json=dict(scenario="database_disconnect",duration=30,concurrency=2,batch=1,total_ids=100000,fault_seconds=3))
        response.raise_for_status()
        run_id = response.json()["id"]
        deadline = time.monotonic()+10
        while True:
            run = client.get(f"/test-api/runs/{run_id}").json()
            if any(e["event"]=="network fault applied" for e in run["timeline"]): break
            assert time.monotonic()<deadline
            time.sleep(.1)
        compose("pause","test-runner")
        try:
            time.sleep(5)
            # Query proxy from the independent watchdog container: no host exposure required.
            compose("exec","-T","watchdog","python","-c",
                    "import httpx; p=httpx.get('http://toxiproxy:8474/proxies/postgres').json(); assert p['enabled'] and not p['toxics']; print('PASS: independent watchdog restored connectivity while runner paused')")
        finally:
            compose("unpause","test-runner")
            client.post(f"/test-api/runs/{run_id}/cancel").raise_for_status()
        deadline=time.monotonic()+15
        while client.get(f"/test-api/runs/{run_id}").json()["status"]=="running":
            assert time.monotonic()<deadline
            time.sleep(.3)
        compose("restart","test-runner")
        deadline=time.monotonic()+15
        while True:
            response=client.get(f"/test-api/runs/{run_id}")
            if response.status_code==200: break
            assert time.monotonic()<deadline
            time.sleep(.5)
        assert response.json()["result"] is not None
        print("PASS: report survives runner restart")


if __name__=="__main__":main()

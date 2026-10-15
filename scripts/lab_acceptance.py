"""Exercise only the local acceptance stack; keep machine-readable reports."""
import json
import os
from pathlib import Path
import time

import httpx


def main():
    token = os.environ["TEST_TOKEN"]
    reports = []
    with httpx.Client(base_url="http://127.0.0.1:18081", timeout=12,
                      headers={"Authorization": "Bearer " + token}) as client:
        assert client.post("/test-api/runs", json={"url": "http://invalid"}).status_code == 400
        for scenario in ("load", "database_latency", "database_disconnect", "cancel"):
            config = dict(scenario="database_disconnect" if scenario == "cancel" else scenario,
                          duration=8, concurrency=4, batch=10, total_ids=10000, fault_seconds=2)
            response = client.post("/test-api/runs", json=config)
            response.raise_for_status()
            run_id = response.json()["id"]
            if scenario == "cancel":
                time.sleep(.5)
                client.post(f"/test-api/runs/{run_id}/cancel").raise_for_status()
            deadline = time.monotonic() + 30
            while True:
                response = client.get(f"/test-api/runs/{run_id}/report")
                response.raise_for_status()
                run = response.json()
                if run["status"] != "running":
                    break
                assert time.monotonic() < deadline, "run exceeded its bound"
                time.sleep(.3)
            assert run["status"] == ("cancelled" if scenario == "cancel" else "completed"), run
            assert run["result"]["duplicate_ids"] == 0, run
            if scenario == "load":
                assert run["result"]["received_unique_ids"] > 0, run
                assert run["result"]["failed_requests"] == 0, run
            recovery_start = time.monotonic()
            while True:
                healthy = all(client.get(f"/nodes/{node}/readyz").status_code == 200 for node in (0, 1))
                if healthy:
                    break
                assert time.monotonic() - recovery_start < 45, "workers did not recover"
                time.sleep(.5)
            # Verify new durable commits after each recovery, not just health endpoints.
            result = client.post("/v1/ids", json=dict(latitude=1,longitude=2,altitude_m=3,speed_mps=4),
                                 headers={"Idempotency-Key": "recovery-" + run_id})
            result.raise_for_status()
            value = result.json()["ids"][0]
            assert client.get("/v1/records/" + value).status_code == 200
            run["verified_recovery_seconds"] = time.monotonic() - recovery_start
            reports.append(run)
            print(json.dumps(run), flush=True)
    return reports


if __name__ == "__main__":
    main()

"""Bounded test runner; targets are deployment configuration, never user URLs."""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

class Lab:
    def __init__(self, path=None, targets=None, proxy=None):
        self.path = Path(path or os.getenv("LAB_STATE", "/results/lab.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.targets = targets or os.getenv("LAB_TARGETS", "http://api-0:8080,http://api-1:8080").split(",")
        self.proxy = proxy or os.getenv("TOXIPROXY_URL", "http://toxiproxy:8474")
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.thread = None
        with self.connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS fault (singleton INTEGER PRIMARY KEY, expires REAL NOT NULL)")
            c.execute("INSERT OR IGNORE INTO fault VALUES (1, 0)")

    def connect(self):
        # Context managers below commit/rollback; explicit closing avoids retained handles.
        from contextlib import contextmanager
        @contextmanager
        def connection():
            c = sqlite3.connect(self.path, timeout=2)
            try:
                with c:
                    yield c
            finally:
                c.close()
        return connection()

    def save(self, run):
        with self.connect() as c:
            c.execute("INSERT INTO runs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data",
                      (run["id"], run["status"], json.dumps(run)))

    def get(self, run_id):
        with self.connect() as c:
            row = c.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest(self):
        with self.connect() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT data FROM runs ORDER BY rowid DESC LIMIT 20")]

    def reset_fault(self):
        with httpx.Client(timeout=2) as client:
            response = client.get(self.proxy + "/proxies/postgres")
            if response.status_code == 404:
                client.post(self.proxy + "/proxies", json={"name":"postgres", "listen":"0.0.0.0:15432", "upstream":"db:5432", "enabled":True}).raise_for_status()
            else:
                response.raise_for_status()
                client.post(self.proxy + "/proxies/postgres", json={"enabled":True}).raise_for_status()
                for toxic in response.json().get("toxics", []):
                    removed = client.delete(self.proxy + "/proxies/postgres/toxics/" + toxic["name"])
                    if removed.status_code != 404:  # watchdog and runner may clean up concurrently
                        removed.raise_for_status()
        with self.connect() as c:
            c.execute("UPDATE fault SET expires=0 WHERE singleton=1")

    def reconcile(self):
        self.reset_fault()
        with self.connect() as c:
            rows = c.execute("SELECT data FROM runs WHERE status='running'").fetchall()
        for row in rows:
            run = json.loads(row[0]); run["status"] = "interrupted"
            run["timeline"].append({"at":time.time(), "event":"runner restarted; connectivity restored"})
            self.save(run)

    def watchdog_once(self):
        with self.connect() as c:
            expires=c.execute("SELECT expires FROM fault WHERE singleton=1").fetchone()[0]
        if expires and time.time() >= expires:
            self.reset_fault()

    def start(self, config):
        allowed = {"scenario", "duration", "concurrency", "batch", "total_ids", "fault_seconds"}
        if not isinstance(config, dict) or set(config)-allowed:
            raise ValueError("unsupported test configuration")
        config = dict(scenario="load", duration=10, concurrency=4, batch=10, total_ids=10000, fault_seconds=3) | config
        if config["scenario"] not in ("load", "database_latency", "database_disconnect"):
            raise ValueError("unsupported scenario")
        for key, maximum in {"duration":60,"concurrency":32,"batch":1000,"total_ids":100000,"fault_seconds":10}.items():
            if type(config[key]) is not int or not 1 <= config[key] <= maximum:
                raise ValueError(f"{key} must be an integer between 1 and {maximum}")
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("a test is already running")
            self.cancel.clear()
            run = {"id":uuid.uuid4().hex,"status":"running","config":config,"timeline":[],"result":None}
            self.save(run)
            self.thread = threading.Thread(target=self.execute, args=(run,), daemon=True)
            self.thread.start()
            return run

    def execute(self, run):
        config = run["config"]
        latencies, seen, errors, issued, duplicate_count = [], set(), 0, 0, 0
        begin = time.monotonic()
        fault_until = 0
        terminal_status = "failed"
        def event(message):
            run["timeline"].append({"at":time.time(),"event":message}); self.save(run)
        try:
            event("test started")
            self.reset_fault()
            if config["scenario"] != "load":
                fault_until = time.time() + config["fault_seconds"]
                # Persist expiry BEFORE touching the proxy so the watchdog can undo a crash.
                with self.connect() as c:
                    c.execute("UPDATE fault SET expires=? WHERE singleton=1", (fault_until,))
                with httpx.Client(timeout=2) as client:
                    if config["scenario"] == "database_disconnect":
                        client.post(self.proxy+"/proxies/postgres",json={"enabled":False}).raise_for_status()
                    else:
                        client.post(self.proxy+"/proxies/postgres/toxics",json={"name":"lab-latency","type":"latency","stream":"downstream","attributes":{"latency":300,"jitter":50}}).raise_for_status()
                event("network fault applied")
            headers = {"Authorization":"Bearer "+os.getenv("API_TOKEN", "")} if os.getenv("API_TOKEN") else {}
            with httpx.Client(timeout=2, headers=headers) as client, ThreadPoolExecutor(max_workers=config["concurrency"]) as pool:
                def send(index, count):
                    start = time.monotonic()
                    try:
                        response = client.post(self.targets[index % len(self.targets)] + "/v1/ids",
                            json={"latitude":12.9716,"longitude":77.5946,"altitude_m":120,"speed_mps":17,"vehicle_id":"LAB-"+run["id"],"count":count},
                            headers={"Idempotency-Key":run["id"]+"-"+str(index)})
                        response.raise_for_status()
                        return time.monotonic()-start, response.json()["ids"], None
                    except Exception as exc:
                        return time.monotonic()-start, [], type(exc).__name__
                index = 0
                while issued < config["total_ids"] and time.monotonic()-begin < config["duration"] and not self.cancel.is_set():
                    if fault_until and time.time() >= fault_until:
                        self.reset_fault(); fault_until=0; event("connectivity restored")
                    tasks = []
                    for _ in range(config["concurrency"]):
                        count = min(config["batch"],config["total_ids"]-issued)
                        if not count: break
                        tasks.append(pool.submit(send,index,count)); issued+=count; index+=1
                    for future in as_completed(tasks):
                        elapsed, values, error = future.result()
                        latencies.append(elapsed)
                        errors += bool(error)
                        for value in values:
                            if value in seen: duplicate_count += 1
                            seen.add(value)
            terminal_status = "cancelled" if self.cancel.is_set() else "completed"
        except Exception as exc:
            terminal_status = "failed"
            event("runner error: " + type(exc).__name__)
        finally:
            try:
                self.reset_fault(); event("cleanup restored connectivity")
            except Exception:
                event("cleanup pending; watchdog will retry")
                terminal_status = "cleanup_pending"
            ordered = sorted(latencies)
            def percentile(p):
                return ordered[int((len(ordered)-1)*p)]*1000 if ordered else None
            elapsed = time.monotonic()-begin
            run["result"] = {"elapsed_seconds":elapsed,"requested_ids":issued,"received_unique_ids":len(seen),"duplicate_ids":duplicate_count,
                "failed_requests":errors,"ids_per_second":len(seen)/elapsed,"latency_ms":{"p50":percentile(.5),"p95":percentile(.95),"p99":percentile(.99)}}
            run["status"] = terminal_status
            self.save(run)


def create_lab_app(lab=None):
    active = lab
    @asynccontextmanager
    async def lifespan(app):
        nonlocal active
        active = active or Lab()
        await asyncio.to_thread(active.reconcile)
        yield
        active.cancel.set()
        if active.thread:
            await asyncio.to_thread(active.thread.join, 5)
        await asyncio.to_thread(active.reset_fault)
    app = FastAPI(title="Spatial ID Test Lab",lifespan=lifespan)

    @app.middleware("http")
    async def authentication(request, call_next):
        token = os.getenv("TEST_TOKEN", "")
        if not token or not hmac.compare_digest(request.headers.get("authorization", ""),"Bearer "+token):
            return JSONResponse({"error":{"code":"unauthorized","message":"enter the configured test token"}},status_code=401)
        return await call_next(request)

    @app.post("/test-api/runs",status_code=202)
    async def start(request: Request):
        try:
            body = await asyncio.wait_for(request.body(),2)
            if len(body)>2048: raise ValueError("body too large")
            return active.start(json.loads(body))
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error":{"message":str(exc)}},status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error":{"message":str(exc)}},status_code=409)

    @app.get("/test-api/runs")
    async def listing(): return active.latest()

    @app.get("/test-api/runs/{run_id}")
    @app.get("/test-api/runs/{run_id}/report")
    async def get(run_id: str):
        result = active.get(run_id)
        return result if result else JSONResponse({"error":{"message":"run not found"}},status_code=404)

    @app.post("/test-api/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        result = active.get(run_id)
        if not result: return JSONResponse({"error":{"message":"run not found"}},status_code=404)
        if result["status"] == "running": active.cancel.set()
        await asyncio.to_thread(active.reset_fault)
        return {"status":"cancellation requested"}
    return app


def watchdog():
    lab = Lab()
    while True:
        try:
            lab.watchdog_once()
        except Exception:
            pass  # Keep trying when the proxy itself is temporarily unavailable.
        time.sleep(.5)

if __name__ == "__main__":
    watchdog()

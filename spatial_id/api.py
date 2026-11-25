"""Managed HTTP boundary. Install the 'service' extra to run this module."""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
import hmac
import json
import logging
import os
from pathlib import Path
import time
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from .core import decode
from .errors import ClockRegressionError, MalformedIdError, SequenceExhaustedError, ValidationError, WorkerLeaseError
from .request_log import RequestLog, VehicleRequestLogging
from .storage import Conflict, Database, RecordStore, StorageUnavailable, json_text, valid_vehicle_id
from .trajectory import astar_route, build_trajectory, trajectory_limits

log = logging.getLogger(__name__)


def create_app(store=None, *, database_url=None, worker_id=None, token=None, ttl_ms=30000, max_batch=1000,
               frontend_dir=None, operator_token=None):
    if type(max_batch) is not int or not 1 <= max_batch <= 1000:
        raise ValidationError("max_batch must be between 1 and 1000")
    registry = CollectorRegistry()
    requests = Counter("spatial_id_requests_total", "Completed requests", ["route", "status"], registry=registry)
    errors = Counter("spatial_id_errors_total", "Failures", ["code"], registry=registry)
    generated = Counter("spatial_id_generated_total", "Committed IDs (excluding replay)", registry=registry)
    replays = Counter("spatial_id_replays_total", "Idempotent replays", registry=registry)
    duration = Histogram("spatial_id_request_seconds", "HTTP request duration", registry=registry)
    commit = Histogram("spatial_id_commit_seconds", "Allocation and durable commit time", registry=registry)
    recent = deque(maxlen=1000)
    started = time.monotonic()
    stats = {"requests": 0, "errors": 0, "generated": 0, "replays": 0}
    api_token = token if token is not None else os.getenv("API_TOKEN", "")
    public_api = os.getenv("PUBLIC_API", "").strip().lower() == "true"
    operator_token = operator_token if operator_token is not None else os.getenv("OPERATOR_TOKEN", "")
    operator_path = "/v1/operator/request-logs"
    frontend = frontend_dir or os.getenv("FRONTEND_DIST")
    if frontend:
        frontend = Path(frontend).resolve()
        if not api_token and not public_api:
            raise ValueError("API_TOKEN is required when serving the hosted dashboard")
        if not (frontend / "index.html").is_file() or not (frontend / "assets").is_dir():
            raise ValueError("FRONTEND_DIST must contain the built dashboard")

    @asynccontextmanager
    async def lifespan(app):
        owned = store is None
        if owned:
            db = Database(database_url or os.getenv("DATABASE_URL", "sqlite:///.spatial-id/state.db"))
            try:
                await asyncio.to_thread(db.migrate)
                configured_worker = worker_id if worker_id is not None else os.getenv("WORKER_ID", "0")
                if configured_worker == "auto":
                    active = await asyncio.to_thread(RecordStore.acquire_available, db, ttl_ms=ttl_ms)
                else:
                    fixed_worker = int(configured_worker) if isinstance(configured_worker, str) else configured_worker
                    active = RecordStore(db, fixed_worker, ttl_ms=ttl_ms)
                    await asyncio.to_thread(active.acquire)
            except BaseException:
                db.close()
                raise
        else:
            active = store
        app.state.store = active
        app.state.request_log = RequestLog(active.db)
        stop_cleanup = asyncio.Event()
        cleanup = asyncio.create_task(app.state.request_log.maintain(stop_cleanup))
        try:
            yield
        finally:
            stop_cleanup.set()
            await cleanup
            if owned:
                await asyncio.to_thread(active.close)

    app = FastAPI(title="Spatial ID", version="0.3.0", lifespan=lifespan)
    app.state.inflight = 0
    Gauge("spatial_id_storage_retries", "Transaction retries in this process", registry=registry).set_function(lambda: app.state.store.retry_count)
    Gauge("spatial_id_sequence_waits", "Sequence capacity waits in this process", registry=registry).set_function(lambda: app.state.store.sequence_waits)

    def failure(status, code, message):
        errors.labels(code).inc()
        stats["errors"] += 1
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                            headers={"Retry-After": "1"} if status in (429, 503) else {})

    @app.middleware("http")
    async def boundary(request, call_next):
        start = time.monotonic()
        public_asset = frontend and request.method in ("GET", "HEAD") and (
            request.url.path == "/" or request.url.path.startswith("/assets/"))
        if request.url.path == operator_path or request.url.path.startswith(operator_path + "/"):
            if not operator_token or operator_token == api_token:
                return failure(403, "operator_disabled", "configure a distinct operator token to view request logs")
            required_token = operator_token
        elif request.url.path == "/v1/config":
            required_token = None
        elif public_api and (request.url.path == "/stats" or request.url.path.split("/")[:3] in (
                ["", "v1", "ids"], ["", "v1", "records"], ["", "v1", "vehicles"])):
            required_token = None
        elif not public_asset and request.url.path not in ("/livez", "/readyz", "/healthz", "/metrics"):
            required_token = api_token
        else:
            required_token = None
        if required_token:
            supplied = request.headers.get("authorization", "").encode()
            if not hmac.compare_digest(supplied, f"Bearer {required_token}".encode()):
                return failure(401, "unauthorized", "a valid bearer token is required")
        if app.state.inflight >= 64:
            return failure(503, "overloaded", "server at capacity; retry later")
        app.state.inflight += 1
        try:
            # Bound upload time and size even for chunked requests.
            if request.method == "POST":
                async def read_body():
                    chunks, total = [], 0
                    async for chunk in request.stream():
                        total += len(chunk)
                        if total > 65536:
                            raise ValidationError("request body exceeds 65536 bytes")
                        chunks.append(chunk)
                    request._body = b"".join(chunks)
                await asyncio.wait_for(read_body(), timeout=2)
            response = await asyncio.wait_for(call_next(request), timeout=max(.001, 5 - (time.monotonic() - start)))
        except asyncio.TimeoutError:
            response = failure(503, "deadline_exceeded", "request timed out; retry generation with the same Idempotency-Key")
        except ValidationError as exc:
            response = failure(413, "request_too_large", str(exc))
        except Exception:
            log.exception("unexpected request failure")
            response = failure(500, "internal_error", "unexpected server error; retry generation with the same Idempotency-Key")
        finally:
            app.state.inflight -= 1
        elapsed = time.monotonic() - start
        recent.append(elapsed)
        duration.observe(elapsed)
        stats["requests"] += 1
        route = request.scope.get("route")
        requests.labels(getattr(route, "path", "unmatched"), str(response.status_code)).inc()
        response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(VehicleRequestLogging)

    for error_class, status, code in [
        (ValidationError, 400, "validation_error"), (MalformedIdError, 400, "validation_error"),
        (Conflict, 409, "idempotency_conflict"), (ClockRegressionError, 503, "clock_regression"),
        (WorkerLeaseError, 503, "worker_lease"), (SequenceExhaustedError, 503, "sequence_exhausted"),
        (StorageUnavailable, 503, "storage_unavailable"),
    ]:
        async def handler(request, exc, status=status, code=code):
            return failure(status, code, str(exc))
        app.add_exception_handler(error_class, handler)

    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(RequestValidationError)
    async def invalid_query(request, exc):
        return failure(400, "validation_error", "invalid path or query parameters")

    async def read_operation(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except (ValidationError, WorkerLeaseError, StorageUnavailable):
            raise
        except Exception as exc:
            log.exception("storage operation failed")
            raise StorageUnavailable("storage operation failed") from exc

    @app.post("/v1/ids", status_code=201)
    async def generate(request: Request):
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return failure(415, "unsupported_media_type", "Content-Type must be application/json")
        try:
            def no_constant(value):
                raise ValueError(value)
            def unique_fields(pairs):
                result = {}
                for k, v in pairs:
                    if k in result:
                        raise ValueError("duplicate JSON field")
                    result[k] = v
                return result
            payload = json.loads(await request.body(), parse_constant=no_constant, object_pairs_hook=unique_fields)
        except (ValueError, UnicodeDecodeError):
            return failure(400, "invalid_json", "expected strict JSON without duplicate fields")
        started_commit = time.monotonic()
        attempt = request.state.attempt
        if isinstance(payload, dict) and valid_vehicle_id(payload.get("vehicle_id")):
            attempt.vehicle_id = payload["vehicle_id"]
        if isinstance(payload, dict) and type(payload.get("count", 1)) is int and payload.get("count", 1) > max_batch:
            raise ValidationError(f"count exceeds configured limit of {max_batch}")
        result, replay = await asyncio.to_thread(app.state.store.generate, payload, request.headers.get("idempotency-key"))
        attempt.point_ids = (result["ids"][0], result["ids"][-1])
        attempt.point_count = result["count"]
        attempt.replayed = replay
        commit.observe(time.monotonic() - started_commit)
        if replay:
            replays.inc(); stats["replays"] += 1
        else:
            generated.inc(result["count"]); stats["generated"] += result["count"]
        return JSONResponse(result, status_code=200 if replay else 201, headers={"Idempotency-Replayed": str(replay).lower()})

    @app.get("/v1/ids/{value}")
    async def inspect(value: str):
        result = decode(value).to_dict()
        result["value"] = str(result["value"])
        return result

    @app.get("/v1/records/{value}")
    async def record(value: str, request: Request):
        decode(value)
        result = await read_operation(app.state.store.record, value)
        if result:
            request.state.attempt.vehicle_id = result.get("vehicle_id")
        return result if result else failure(404, "not_found", "record not found")

    @app.get("/v1/vehicles/{vehicle}/records")
    async def history(vehicle: str, limit: int = 100, cursor: str | None = None, from_ms: int | None = None, to_ms: int | None = None):
        return await read_operation(app.state.store.history, vehicle, limit, cursor, from_ms, to_ms)

    @app.get("/v1/vehicles/{vehicle}/latest")
    async def latest(vehicle: str):
        result = await read_operation(app.state.store.history, vehicle, 1, latest=True)
        return result["items"][0] if result["items"] else failure(404, "not_found", "vehicle has no records")

    async def vehicle_trajectory(vehicle, limit, max_gap_ms, max_speed_mps, max_distance_m, from_ms, to_ms, latest=False):
        config = trajectory_limits(limit=limit, max_gap_ms=max_gap_ms,
                                   max_speed_mps=max_speed_mps, max_distance_m=max_distance_m)
        page = await read_operation(app.state.store.history, vehicle, config["limit"], None, from_ms, to_ms, latest=latest)
        result = build_trajectory(page["items"], **config)
        result["vehicle_id"] = vehicle
        has_more_records = page["next_cursor"] is not None
        result["summary"]["has_more_records"] = has_more_records
        result["summary"]["truncated"] = has_more_records
        return result

    @app.get("/v1/vehicles/{vehicle}/trajectory")
    async def trajectory(vehicle: str, limit: int = 1000, max_gap_ms: int = 300000,
                         max_speed_mps: float = 100, max_distance_m: float = 50000,
                         from_ms: int | None = None, to_ms: int | None = None,
                         window: Literal["earliest", "latest"] = "earliest"):
        return await vehicle_trajectory(vehicle, limit, max_gap_ms, max_speed_mps,
                                        max_distance_m, from_ms, to_ms, latest=window == "latest")

    @app.get("/v1/vehicles/{vehicle}/route")
    async def route(vehicle: str, start_id: str | None = None, end_id: str | None = None,
                    limit: int = 1000, max_gap_ms: int = 300000,
                    max_speed_mps: float = 100, max_distance_m: float = 50000,
                    from_ms: int | None = None, to_ms: int | None = None):
        result = await vehicle_trajectory(vehicle, limit, max_gap_ms, max_speed_mps,
                                          max_distance_m, from_ms, to_ms)
        return {"vehicle_id": vehicle, "route": astar_route(result, start_id, end_id),
                "limits": result["limits"], "summary": result["summary"]}

    @app.get("/v1/vehicles/{vehicle}/export")
    async def export(vehicle: str):
        first = await read_operation(app.state.store.history, vehicle, 1000)
        async def rows():
            page = first
            while True:
                for item in page["items"]:
                    yield json_text(item) + "\n"
                if not page["next_cursor"]:
                    break
                page = await read_operation(app.state.store.history, vehicle, 1000, page["next_cursor"])
        return StreamingResponse(rows(), media_type="application/x-ndjson", headers={"Content-Disposition": 'attachment; filename="history.jsonl"'})

    @app.get(operator_path)
    async def request_logs(limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
                           vehicle_id: str | None = None, status: int | None = Query(None, ge=100, le=599),
                           request_id: str | None = None,
                           from_ms: int | None = Query(None, ge=0, le=253402300799999),
                           to_ms: int | None = Query(None, ge=0, le=253402300799999)):
        return await read_operation(app.state.request_log.page, limit, cursor, vehicle_id, status,
                                    request_id, from_ms, to_ms)

    @app.get(operator_path + "/{request_id}/points")
    async def request_points(request_id: str):
        items = await read_operation(app.state.request_log.points, request_id)
        return {"items": items} if items else failure(404, "not_found", "no saved points for this request")

    @app.get("/v1/config")
    async def configuration():
        return {"public_api": public_api}

    @app.get("/livez")
    async def live():
        return {"status": "alive"}

    @app.get("/readyz")
    @app.get("/healthz")
    async def ready():
        return await read_operation(app.state.store.ready)

    @app.get("/metrics")
    async def metrics():
        return Response(generate_latest(registry), media_type="text/plain; version=0.0.4")

    @app.get("/stats")
    async def statistics():
        ordered = sorted(recent)
        def percentile(p):
            return ordered[min(len(ordered)-1, int((len(ordered)-1)*p))]*1000 if ordered else None
        return dict(stats, worker_id=app.state.store.worker_id, uptime_seconds=time.monotonic()-started,
                    latency_ms={"p50": percentile(.5), "p95": percentile(.95), "p99": percentile(.99)})

    if frontend:
        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        async def dashboard():
            return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-store"})

        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="dashboard-assets")

    return app

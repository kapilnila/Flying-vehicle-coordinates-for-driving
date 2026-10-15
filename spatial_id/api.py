"""Managed HTTP boundary. Install the 'service' extra to run this module."""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
import hmac
import json
import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from .core import decode
from .errors import ClockRegressionError, MalformedIdError, SequenceExhaustedError, ValidationError, WorkerLeaseError
from .storage import Conflict, Database, RecordStore, StorageUnavailable, json_text

log = logging.getLogger(__name__)


def create_app(store=None, *, database_url=None, worker_id=None, token=None, ttl_ms=30000, max_batch=1000):
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

    @asynccontextmanager
    async def lifespan(app):
        owned = store is None
        if owned:
            db = Database(database_url or os.getenv("DATABASE_URL", "sqlite:///.spatial-id/state.db"))
            try:
                await asyncio.to_thread(db.migrate)
                active = RecordStore(db, worker_id if worker_id is not None else int(os.getenv("WORKER_ID", "0")), ttl_ms=ttl_ms)
                await asyncio.to_thread(active.acquire)
            except BaseException:
                db.close()
                raise
        else:
            active = store
        app.state.store = active
        try:
            yield
        finally:
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
        if api_token and request.url.path not in ("/livez", "/readyz", "/healthz", "/metrics"):
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied, f"Bearer {api_token}"):
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
        if isinstance(payload, dict) and type(payload.get("count", 1)) is int and payload.get("count", 1) > max_batch:
            raise ValidationError(f"count exceeds configured limit of {max_batch}")
        result, replay = await asyncio.to_thread(app.state.store.generate, payload, request.headers.get("idempotency-key"))
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
    async def record(value: str):
        decode(value)
        result = await read_operation(app.state.store.record, value)
        return result if result else failure(404, "not_found", "record not found")

    @app.get("/v1/vehicles/{vehicle}/records")
    async def history(vehicle: str, limit: int = 100, cursor: str | None = None, from_ms: int | None = None, to_ms: int | None = None):
        return await read_operation(app.state.store.history, vehicle, limit, cursor, from_ms, to_ms)

    @app.get("/v1/vehicles/{vehicle}/latest")
    async def latest(vehicle: str):
        result = await read_operation(app.state.store.history, vehicle, 1, latest=True)
        return result["items"][0] if result["items"] else failure(404, "not_found", "vehicle has no records")

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

    return app

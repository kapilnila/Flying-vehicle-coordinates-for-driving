"""Dependency-free HTTP API for the spatial ID generator."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import signal
import threading
import time
from typing import Callable, Protocol
from urllib.parse import unquote, urlsplit

from .core import VERSION, decode
from .errors import (
    ClockRegressionError,
    MalformedIdError,
    RestartSafetyError,
    SequenceExhaustedError,
    SpatialIdError,
    ValidationError,
    WorkerLeaseError,
)
from .metrics import ServiceMetrics

MAX_REQUEST_BYTES = 64 * 1024
DECODE_PATH = re.compile(r"^/v1/ids/([^/]+)$")
Generate = Callable[[float, float, float, float, int], str]


class Generator(Protocol):
    worker_id: int

    def generate(
        self,
        latitude: float,
        longitude: float,
        altitude_m: float,
        speed_mps: float,
        vehicle_class: int = 0,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    status: int
    code: str
    message: str


class SpatialIdApplication:
    def __init__(self, generator: Generator, *, max_batch: int = 1_000) -> None:
        if not 1 <= max_batch <= 10_000:
            raise ValueError("max_batch must be from 1 to 10000")
        self.generator = generator
        self.max_batch = max_batch
        self.metrics = ServiceMetrics()

    def generate(self, payload: object) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid_request", "request body must be a JSON object")
        allowed = {"latitude", "longitude", "altitude_m", "speed_mps", "vehicle_class", "count"}
        unknown = set(payload) - allowed
        if unknown:
            raise ApiError(400, "invalid_request", f"unknown fields: {', '.join(sorted(unknown))}")
        missing = {"latitude", "longitude", "altitude_m", "speed_mps"} - set(payload)
        if missing:
            raise ApiError(400, "invalid_request", f"missing fields: {', '.join(sorted(missing))}")
        count = payload.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= self.max_batch:
            raise ApiError(400, "invalid_request", f"count must be an integer from 1 to {self.max_batch}")
        vehicle_class = payload.get("vehicle_class", 0)
        values = [
            self.generator.generate(
                payload["latitude"],  # type: ignore[arg-type]
                payload["longitude"],  # type: ignore[arg-type]
                payload["altitude_m"],  # type: ignore[arg-type]
                payload["speed_mps"],  # type: ignore[arg-type]
                vehicle_class,  # type: ignore[arg-type]
            )
            for _ in range(count)
        ]
        self.metrics.generated(count)
        return {"ids": values, "count": count, "worker_id": self.generator.worker_id}

    def health(self) -> dict[str, object]:
        lease = getattr(self.generator, "lease", None)
        if lease is not None:
            lease.assert_valid()
        return {"status": "ok", "version": VERSION, "worker_id": self.generator.worker_id}


class SpatialIdHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], application: SpatialIdApplication) -> None:
        self.application = application
        super().__init__(address, SpatialIdRequestHandler)


class SpatialIdRequestHandler(BaseHTTPRequestHandler):
    server: SpatialIdHttpServer
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        started = time.perf_counter()
        route = "not_found"
        status = 500
        try:
            if urlsplit(self.path).path != "/v1/ids":
                raise ApiError(404, "not_found", "route not found")
            route = "/v1/ids"
            payload = self._read_json()
            result = self.server.application.generate(payload)
            status = 201
            self._send_json(status, result)
        except Exception as exc:
            status = self._send_error(exc)
        finally:
            self.server.application.metrics.observe_request("POST", route, status, time.perf_counter() - started)

    def do_GET(self) -> None:
        started = time.perf_counter()
        route = "not_found"
        status = 500
        try:
            path = urlsplit(self.path).path
            if path == "/healthz":
                route = "/healthz"
                result = self.server.application.health()
                status = 200
                self._send_json(status, result)
            elif path == "/metrics":
                route = "/metrics"
                body = self.server.application.metrics.prometheus().encode()
                status = 200
                self._send(status, body, "text/plain; version=0.0.4; charset=utf-8")
            else:
                match = DECODE_PATH.fullmatch(path)
                if match is None:
                    raise ApiError(404, "not_found", "route not found")
                route = "/v1/ids/{id}"
                result = decode(unquote(match.group(1))).to_dict()
                status = 200
                self._send_json(status, result)
        except Exception as exc:
            status = self._send_error(exc)
        finally:
            self.server.application.metrics.observe_request("GET", route, status, time.perf_counter() - started)

    def _read_json(self) -> object:
        if self.headers.get_content_type() != "application/json":
            raise ApiError(415, "unsupported_media_type", "Content-Type must be application/json")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(411, "length_required", "Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", "invalid Content-Length") from exc
        if not 0 <= length <= MAX_REQUEST_BYTES:
            raise ApiError(413, "request_too_large", "request body exceeds 65536 bytes")
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(400, "invalid_json", "request body is not valid JSON") from exc

    def _send_error(self, exc: Exception) -> int:
        if isinstance(exc, ApiError):
            status, code, message = exc.status, exc.code, exc.message
        elif isinstance(exc, (ValidationError, MalformedIdError)):
            status, code, message = 400, "validation_error", str(exc)
        elif isinstance(exc, ClockRegressionError):
            status, code, message = 503, "clock_regression", str(exc)
        elif isinstance(exc, RestartSafetyError):
            status, code, message = 503, "restart_safety", str(exc)
        elif isinstance(exc, WorkerLeaseError):
            status, code, message = 503, "worker_lease", str(exc)
        elif isinstance(exc, SequenceExhaustedError):
            status, code, message = 503, "sequence_exhausted", str(exc)
        elif isinstance(exc, SpatialIdError):
            status, code, message = 500, "spatial_id_error", str(exc)
        else:
            status, code, message = 500, "internal_error", "internal server error"
        self.server.application.metrics.error(code)
        self._send_json(status, {"error": {"code": code, "message": message}})
        return status

    def _send_json(self, status: int, payload: object) -> None:
        self._send(status, json.dumps(payload, separators=(",", ":")).encode(), "application/json")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Keep library embedding quiet; production access logging belongs at the
        # reverse proxy or can be added by supplying a handler subclass.
        return


def create_server(
    host: str,
    port: int,
    generator: Generator,
    *,
    max_batch: int = 1_000,
) -> SpatialIdHttpServer:
    return SpatialIdHttpServer((host, port), SpatialIdApplication(generator, max_batch=max_batch))


def run_server(server: SpatialIdHttpServer) -> None:
    """Serve until SIGINT/SIGTERM, then drain handlers and close cleanly."""

    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    previous: dict[int, object] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)
    server.timeout = 0.5
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        server.server_close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)

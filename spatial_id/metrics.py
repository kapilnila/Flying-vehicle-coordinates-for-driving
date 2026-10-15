"""Small dependency-free Prometheus metrics registry for the HTTP service."""

from __future__ import annotations

from collections import Counter
import threading
import time


class ServiceMetrics:
    def __init__(self) -> None:
        self._started = time.monotonic()
        self._lock = threading.Lock()
        self._requests: Counter[tuple[str, str, int]] = Counter()
        self._errors: Counter[str] = Counter()
        self._generated = 0
        self._latency_seconds = 0.0

    def observe_request(self, method: str, route: str, status: int, elapsed: float) -> None:
        with self._lock:
            self._requests[(method, route, status)] += 1
            self._latency_seconds += elapsed

    def generated(self, count: int) -> None:
        with self._lock:
            self._generated += count

    def error(self, code: str) -> None:
        with self._lock:
            self._errors[code] += 1

    def prometheus(self) -> str:
        with self._lock:
            requests = self._requests.copy()
            errors = self._errors.copy()
            generated = self._generated
            latency = self._latency_seconds
        lines = [
            "# HELP spatial_id_uptime_seconds Process uptime.",
            "# TYPE spatial_id_uptime_seconds gauge",
            f"spatial_id_uptime_seconds {time.monotonic() - self._started:.6f}",
            "# HELP spatial_id_generated_total IDs returned successfully.",
            "# TYPE spatial_id_generated_total counter",
            f"spatial_id_generated_total {generated}",
            "# HELP spatial_id_http_requests_total HTTP requests by route and status.",
            "# TYPE spatial_id_http_requests_total counter",
        ]
        for (method, route, status), count in sorted(requests.items()):
            lines.append(
                'spatial_id_http_requests_total'
                f'{{method="{method}",route="{route}",status="{status}"}} {count}'
            )
        lines.extend(
            [
                "# HELP spatial_id_http_request_duration_seconds_sum Total HTTP request time.",
                "# TYPE spatial_id_http_request_duration_seconds_sum counter",
                f"spatial_id_http_request_duration_seconds_sum {latency:.9f}",
                "# HELP spatial_id_errors_total Generator and request errors by code.",
                "# TYPE spatial_id_errors_total counter",
            ]
        )
        for code, count in sorted(errors.items()):
            lines.append(f'spatial_id_errors_total{{code="{code}"}} {count}')
        return "\n".join(lines) + "\n"


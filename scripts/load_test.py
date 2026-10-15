"""Simple dependency-free load test for a running spatial ID service."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import time
from urllib.request import Request, urlopen


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Load test POST /v1/ids")
    parser.add_argument("--url", default="http://127.0.0.1:8080/v1/ids")
    parser.add_argument("--requests", type=int, default=1_000)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--batch", type=int, default=10)
    args = parser.parse_args()
    if args.requests <= 0 or args.concurrency <= 0 or args.batch <= 0:
        parser.error("requests, concurrency, and batch must be positive")

    body = json.dumps(
        {
            "latitude": 12.9716,
            "longitude": 77.5946,
            "altitude_m": 120.4,
            "speed_mps": 17.8,
            "vehicle_class": 3,
            "count": args.batch,
        }
    ).encode()

    def send(_: int) -> tuple[float, int, int]:
        request = Request(args.url, data=body, headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.load(response)
                return time.perf_counter() - started, response.status, len(payload["ids"])
        except Exception:
            return time.perf_counter() - started, 0, 0

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(send, range(args.requests)))
    elapsed = time.perf_counter() - started
    latencies = [result[0] for result in results]
    generated = sum(result[2] for result in results)
    errors = sum(result[1] != 201 for result in results)
    print(
        json.dumps(
            {
                "requests": args.requests,
                "concurrency": args.concurrency,
                "batch": args.batch,
                "elapsed_seconds": elapsed,
                "request_rate": args.requests / elapsed,
                "id_rate": generated / elapsed,
                "errors": errors,
                "latency_ms": {
                    "p50": percentile(latencies, 0.50) * 1000,
                    "p95": percentile(latencies, 0.95) * 1000,
                    "p99": percentile(latencies, 0.99) * 1000,
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

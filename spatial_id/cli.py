"""Command-line interface for generating, decoding, and benchmarking IDs."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time

from .coordination import DurableSpatialIdGenerator, SQLiteWorkerCoordinator
from .core import SpatialIdGenerator, decode
from .errors import SpatialIdError, ValidationError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spatial-id", description="Generate and inspect V1 spatial IDs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="generate one or more IDs")
    _add_spatial_arguments(generate)
    generate.add_argument("--worker", type=int, required=True, help="exclusive worker ID (0-1023)")
    generate.add_argument("--count", type=_positive_int, default=1)
    generate.set_defaults(handler=_generate)

    inspect = subparsers.add_parser("decode", help="decode a canonical ID")
    inspect.add_argument("id")
    inspect.set_defaults(handler=_decode)

    benchmark = subparsers.add_parser("benchmark", help="measure threaded generator throughput")
    _add_spatial_arguments(benchmark)
    benchmark.add_argument("--worker", type=int, default=0)
    benchmark.add_argument("--count", type=_positive_int, default=100_000)
    benchmark.add_argument("--threads", type=_positive_int, default=1)
    benchmark.set_defaults(handler=_benchmark)

    serve = subparsers.add_parser("serve", help="run the restart-safe HTTP service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--worker", type=int, required=True)
    serve.add_argument("--state", type=Path, default=Path(".spatial-id/state.db"))
    serve.add_argument("--lease-ttl-ms", type=_positive_int, default=30_000)
    serve.add_argument("--max-batch", type=_positive_int, default=1_000)
    serve.add_argument("--database-url", default=None)
    serve.set_defaults(handler=_serve)
    return parser


def _add_spatial_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--altitude", type=float, required=True, help="altitude in metres")
    parser.add_argument("--speed", type=float, required=True, help="speed in metres/second")
    parser.add_argument("--vehicle-class", type=int, default=0)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _generator(args: argparse.Namespace) -> SpatialIdGenerator:
    return SpatialIdGenerator(args.worker)


def _generate(args: argparse.Namespace) -> int:
    generator = _generator(args)
    for _ in range(args.count):
        value = generator.generate(args.lat, args.lon, args.altitude, args.speed, args.vehicle_class)
        print(json.dumps(decode(value).to_dict(), separators=(",", ":")))
    return 0


def _decode(args: argparse.Namespace) -> int:
    print(json.dumps(decode(args.id).to_dict(), indent=2))
    return 0


def _benchmark(args: argparse.Namespace) -> int:
    generator = _generator(args)
    base, remainder = divmod(args.count, args.threads)
    work = [base + (1 if index < remainder else 0) for index in range(args.threads)]
    work = [size for size in work if size]

    def generate_batch(size: int) -> list[str]:
        return [
            generator.generate(args.lat, args.lon, args.altitude, args.speed, args.vehicle_class)
            for _ in range(size)
        ]

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        batches = list(pool.map(generate_batch, work))
    elapsed = time.perf_counter() - started
    ids = [value for batch in batches for value in batch]
    unique_count = len(set(ids))
    sorted_timestamps = [decode(value).timestamp_ms for value in sorted(ids)]
    time_ordered = all(a <= b for a, b in zip(sorted_timestamps, sorted_timestamps[1:]))
    report = {
        "count": len(ids),
        "threads": args.threads,
        "elapsed_seconds": elapsed,
        "ids_per_second": len(ids) / elapsed if elapsed else None,
        "duplicate_count": len(ids) - unique_count,
        "canonical_width_valid": all(len(value) == 32 for value in ids),
        "hex_order_preserves_timestamp_order": time_ordered,
    }
    print(json.dumps(report, indent=2))
    return 0


def _serve(args: argparse.Namespace) -> int:
    if not 0 <= args.port <= 65535:
        raise ValidationError("port must be from 0 to 65535")
    import uvicorn
    from .api import create_app
    app = create_app(database_url=args.database_url or f"sqlite:///{args.state}", worker_id=args.worker,
                     ttl_ms=args.lease_ttl_ms, max_batch=args.max_batch)
    uvicorn.run(app, host=args.host, port=args.port, timeout_graceful_shutdown=10,
                timeout_keep_alive=5, limit_concurrency=64)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except SpatialIdError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())

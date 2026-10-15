from __future__ import annotations

import json
import subprocess
import sys


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "spatial_id", *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def test_generate_and_decode() -> None:
    generated = run_cli(
        "generate",
        "--lat", "12.9716",
        "--lon", "77.5946",
        "--altitude", "120.4",
        "--speed", "17.8",
        "--vehicle-class", "3",
        "--worker", "21",
    )
    assert generated.returncode == 0, generated.stderr
    payload = json.loads(generated.stdout)
    assert payload["worker_id"] == 21
    assert len(payload["id"]) == 32

    decoded = run_cli("decode", payload["id"])
    assert decoded.returncode == 0, decoded.stderr
    assert json.loads(decoded.stdout)["id"] == payload["id"]


def test_invalid_cli_input_returns_error() -> None:
    result = run_cli(
        "generate",
        "--lat", "91",
        "--lon", "0",
        "--altitude", "0",
        "--speed", "0",
        "--worker", "0",
    )
    assert result.returncode == 2
    assert "latitude" in result.stderr


def test_benchmark_report() -> None:
    result = run_cli(
        "benchmark",
        "--lat", "0",
        "--lon", "0",
        "--altitude", "0",
        "--speed", "0",
        "--count", "2000",
        "--threads", "2",
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["count"] == 2000
    assert report["duplicate_count"] == 0
    assert report["canonical_width_valid"] is True
    assert report["hex_order_preserves_timestamp_order"] is True


# Verification report

Executed locally through 21 October 2026. These are measured smoke-test results, not
production capacity guarantees. No hosted CI execution is claimed.

## Executed checks

| Check | Result |
|---|---|
| Windows Python 3.11 local suite | 76 passed; 3 live-service tests skipped |
| Linux Python 3.12 full suite with real services | 79 passed, no skipped tests |
| Real HTTP across two PostgreSQL-backed replicas | All skipped tests run separately: 3 passed, including client disconnect followed by retry |
| Chromium browser acceptance | 4 passed: generation/retry/decode/history/reload; mobile disconnected health; Test Lab/report; trajectory/A* path |
| Frontend TypeScript and production build | Passed |
| Trajectory validation | Haversine + altitude distance, every break reason, ordering, limits, API, and A* route passed |
| npm dependency audit after Playwright update | Zero reported vulnerabilities |
| API kill/restart and database restart | Passed on 13 October; acknowledged history and idempotent replay retained |
| Bounded load, latency, disconnection, cancellation | All passed; fresh committed record retrieved after every scenario |
| Independent watchdog with runner paused | Proxy restored; runner unpaused and cancelled safely |
| Runner restart | Existing report retained and retrieved |
| PostgreSQL binary backup/restore | 19,659 records, 2,766 request results, 3 allocation rows restored; no orphan records |
| Standalone SQLite HTTP and restart | 3 HTTP tests passed; history and original retry response survived API restart |

The browser tests found an HTTP-origin `randomUUID` issue; generation now uses
128 cryptographically random bits from `getRandomValues`. Other hardening covers
oversized numeric inputs, safe unexpected-error responses, configured batch
limits, a total request deadline, concurrent fault cleanup, and publishing test
completion only after its report is ready.

The Python container suite emits a Starlette deprecation warning about its
httpx-based test client. Tests pass; runtime HTTP traffic uses Uvicorn.

## Live fault results

Two API workers, Toxiproxy, PostgreSQL 18, four concurrent requests, batch 10,
eight-second run budget, maximum 10,000 requested IDs, two-second faults.
The duration budget can stop a run before the maximum ID count is reached.

| Scenario | Requested IDs | Received unique IDs | Failed requests | Duplicate IDs |
|---|---:|---:|---:|---:|
| Load | 7,080 | 7,080 | 0 | 0 |
| Database latency | 5,440 | 5,400 | 4 | 0 |
| Database disconnect | 4,640 | 4,600 | 4 | 0 |
| Cancel during disconnect | 40 | 20 | 2 | 0 |

Failed HTTP requests can have uncertain commit outcomes; received IDs are not
the total number of durable records. Applications must retry their original
key to resolve such outcomes. The lab reports these requests as failures rather
than silently assuming that no data committed. Readiness and a fresh durable
write were verified after every scenario. Reports remain in the lab-results
volume and can be downloaded in Test Lab.

## Benchmark results

Repeated on 21 October 2026 with no artificial faults. The raw CLI ran on
Windows 10 build 26200 with Python 3.11.8. Durable benchmarks ran in Docker
Desktop / WSL2 Linux 6.6.87.2 with Python 3.12.15. SQLite used WAL/FULL in a
temporary file; PostgreSQL used synchronous commits through the fault-free
local proxy. The host also ran the development containers, so these are noisy
development measurements rather than production capacity guarantees.

Raw generator, 100,000 IDs:

| Threads | IDs/sec | Seconds | Duplicates |
|---:|---:|---:|---:|
| 1 | 89,959 | 1.112 | 0 |
| 8 | 15,066 | 6.637 | 0 |

The shared generator deliberately serializes timestamp and sequence allocation.
On this workload, additional threads added lock and scheduling contention rather
than throughput. Scale with distinct worker-owned processes instead of threads
sharing one generator.

Direct generation/storage, 10,000 IDs in 100-ID batches:

| Mode | IDs/sec | p50 batch ms | p95 batch ms | p99 batch ms | Duplicates |
|---|---:|---:|---:|---:|---:|
| Core memory in container | 40,558 | 1.453 | 8.793 | 9.962 | 0 |
| SQLite durable | 6,498 | 11.300 | 40.002 | 49.579 | 0 |
| PostgreSQL durable | 1,293 | 75.033 | 103.829 | 120.517 | 0 |

End-to-end through Nginx, two APIs and PostgreSQL, 16 concurrent clients:

| Batch | Requests | IDs | IDs/sec | p50 request ms | p95 request ms | p99 request ms |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 1,000 | 10,000 | 494 | 272.506 | 544.659 | 1,418.148 |
| 100 | 100 | 10,000 | 2,393 | 389.582 | 1,700.123 | 2,145.770 |

Both HTTP runs returned every requested ID, zero duplicate IDs, zero failed
requests, and only canonical 32-character IDs. Larger batches raised individual
request latency but increased ID throughput about 4.8x by amortizing transaction
and HTTP overhead. Concurrent request latency includes queueing. Repeat on target
hardware with a longer warm-up and sustained run before capacity planning.

## Reproduce

From the repository root in PowerShell:

```powershell
$env:WEB_PORT='18081'
$env:TEST_TOKEN='choose-a-local-test-token'
docker compose -p spatial-id-acceptance -f compose.yaml -f compose.test.yaml up -d --build --wait
docker compose -p spatial-id-acceptance -f compose.yaml -f compose.test.yaml --profile test run --build --no-deps --rm integration
docker compose -p spatial-id-acceptance -f compose.yaml -f compose.test.yaml --profile test run --build --no-deps --rm browser-tests
.venv\Scripts\python -m scripts.lab_acceptance
.venv\Scripts\python -m scripts.watchdog_acceptance
.venv\Scripts\python -m scripts.postgres_restore_test
.venv\Scripts\python -m scripts.failure_test
.venv\Scripts\python -m scripts.benchmark_storage --count 10000 --batch 100
.venv\Scripts\python -m scripts.load_test --url http://127.0.0.1:18081/v1/ids --requests 1000 --concurrency 16 --batch 10
```

For the PostgreSQL comparison, execute `scripts.benchmark_storage` inside api-0
and pass that container's `DATABASE_URL` as `--database-url`. Repeat with
`--batch 1` as the single-record baseline. Benchmark worker 999 must be free.
Do not run fault scenarios concurrently with benchmark or browser tests.

## Scope of evidence

Disk-full and corruption coverage injects SQLite exceptions at transaction
boundaries; it does not damage a real disk. Crash tests use abrupt subprocess
exit before commit and after commit before response delivery. SQLite backup
restore and PostgreSQL binary dump/restore are verified. Restored worker state
was not used for generation: production recovery still requires the documented
safe timestamp barrier and operator control.
This is not proof against every hardware, kernel, filesystem or network failure.
No external security audit, container-image vulnerability scan, sustained
production load test, database HA or multi-region failover is claimed.

The Sites skill's local-preview checks were used to inspect desktop/mobile
screenshots and keep this application local; no hosted Site was created.

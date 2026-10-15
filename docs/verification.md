# Verification report

Executed locally on 14 October 2026. These are measured smoke-test results, not
production capacity guarantees. No hosted CI execution is claimed.

## Executed checks

| Check | Result |
|---|---|
| Windows Python 3.11 local suite | 69 passed; 3 live-service tests skipped |
| Linux Python 3.12 full suite with real services | 72 passed, no skipped tests |
| Real HTTP across two PostgreSQL-backed replicas | All skipped tests run separately: 3 passed, including client disconnect followed by retry |
| Chromium browser acceptance | 3 passed: generation/retry/decode/history/reload; mobile disconnected health; Test Lab/report |
| Frontend TypeScript and production build | Passed |
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

Docker Desktop / WSL2 Linux 6.6.87.2, Python 3.12.15, 1,000 IDs per mode,
one benchmark producer, worker 999. SQLite uses WAL/FULL in a temporary local
file; PostgreSQL uses synchronous commits through the fault-free local proxy.
The database stores original records and idempotency responses in both modes.
Memory mode is the non-durable library baseline. No artificial faults were active.
The host also ran other containers; these are small, noisy development samples.

| Mode | Batch | IDs/sec | p50 batch ms | p95 batch ms | p99 batch ms |
|---|---:|---:|---:|---:|---:|
| Core memory | 1 | 66,608 | 0.011 | 0.018 | 0.088 |
| Core memory | 100 | 66,043 | 1.444 | 1.641 | 1.641 |
| SQLite durable | 1 | 396 | 1.509 | 7.367 | 13.371 |
| SQLite durable | 100 | 8,653 | 10.437 | 14.611 | 14.611 |
| PostgreSQL durable | 1 | 62.6 | 14.663 | 23.630 | 38.046 |
| PostgreSQL durable | 100 | 2,830 | 34.181 | 39.273 | 39.273 |

Every benchmark returned zero duplicates. Batching improved aggregate durable
throughput about 22x for SQLite and 45x for PostgreSQL in these samples without
weakening commit settings. Latencies are per batch, not per ID. Batch-100 runs
have only ten samples: p99 is not a statistically reliable tail estimate.
Repeat with at least 100,000 IDs for capacity planning.

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
.venv\Scripts\python -m scripts.benchmark_storage --count 1000 --batch 100
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

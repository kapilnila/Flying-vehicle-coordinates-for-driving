# Testing strategy

The test plan is organized around invariants and failure boundaries rather than
only happy-path route coverage. Unit tests are fast and deterministic; Docker
tests prove behavior across real processes, networks, browsers, and PostgreSQL.

## Test layers

| Layer | Main coverage | Command |
|---|---|---|
| Core unit | Masks, shifts, boundaries, decoding, clock/sequence behavior | `python -m pytest tests/test_core.py` |
| Storage | Atomicity, leases, fencing, retries, history, migrations | `python -m pytest tests/test_storage.py` |
| API | Strict JSON, auth, deadlines, overload, error shape | `python -m pytest tests/test_api.py` |
| Crash | Exit before commit and after commit/before response | `python -m pytest tests/test_crashes.py` |
| Test Lab | Bounds, reconciliation, one-run limit, watchdog expiry | `python -m pytest tests/test_lab.py` |
| Live HTTP | Two replicas, retry contention, client disconnect | `python -m pytest tests/test_network.py` in Compose |
| Browser | Complete journey, mobile failure state, lab report | Playwright container |
| Host failure | API kill/restart and PostgreSQL restart | `python -m scripts.failure_test` |
| Recovery | Network faults, watchdog, backup/restore, SQLite restart | Acceptance scripts |

## Local Python suite

```powershell
.\.venv\Scripts\python -m pytest -ra
```

Live network tests skip when `SPATIAL_TEST_URL` is absent. This is expected for a
pure local unit run and must not be confused with full acceptance coverage.

Run one file or case while developing:

```powershell
.\.venv\Scripts\python -m pytest tests\test_storage.py -q
.\.venv\Scripts\python -m pytest tests\test_api.py -k deadline -vv
```

## Full networked acceptance stack

```powershell
$env:WEB_PORT = '18081'
$env:TEST_TOKEN = 'choose-a-local-test-token'
docker compose -p spatial-id-acceptance `
  -f compose.yaml -f compose.test.yaml `
  up -d --build --wait
```

Run the container suite:

```powershell
docker compose -p spatial-id-acceptance `
  -f compose.yaml -f compose.test.yaml --profile test `
  run --build --no-deps --rm integration
```

Run Chromium end-to-end tests:

```powershell
docker compose -p spatial-id-acceptance `
  -f compose.yaml -f compose.test.yaml --profile test `
  run --build --no-deps --rm browser-tests
```

Screenshots and failure traces are written under `frontend/test-results/` and
ignored by Git. Browser tests cover:

- generation, identical retry, decode, history, latest record and reload;
- mobile layout with disconnected health requests;
- a bounded Test Lab run and zero-duplicate report.

## Failure and recovery workflows

Run these sequentially against the acceptance project. They intentionally change
test connectivity or restart test containers.

```powershell
.\.venv\Scripts\python -m scripts.lab_acceptance
.\.venv\Scripts\python -m scripts.watchdog_acceptance
.\.venv\Scripts\python -m scripts.postgres_restore_test
```

`lab_acceptance` runs load, latency, disconnection, and cancellation; after each
scenario it waits for readiness and proves a fresh record can be committed and
retrieved. `watchdog_acceptance` pauses the runner during a database fault and
proves the independent watchdog restores the proxy, then restarts the runner and
checks report persistence. `postgres_restore_test` restores a binary dump into a
uniquely named disposable database, checks counts and referential integrity, and
removes only that database.

The separate kill/restart workflow always uses project name
`spatial-id-failure-test` and port 18080:

```powershell
.\.venv\Scripts\python -m scripts.failure_test
```

It stops its containers afterward and retains its volume. It never removes the
development database volume.

Standalone SQLite restart test:

```powershell
$env:WEB_PORT = '18082'
docker compose -p spatial-id-sqlite-acceptance -f compose.local.yaml up -d --build
.\.venv\Scripts\python -m scripts.standalone_acceptance
docker compose -p spatial-id-sqlite-acceptance -f compose.local.yaml down
```

## What fault tests assert

- No duplicate canonical IDs.
- Acknowledged batches have all records.
- A failed transaction has no partial records.
- The same idempotency key/payload returns the original result.
- A changed payload with the same key conflicts.
- Stale or expired owners cannot commit.
- Readiness becomes unavailable when ownership/storage is unsafe.
- Latency and disconnect failures remain bounded.
- Connectivity is restored after cancellation, expiry, or runner interruption.
- Reports survive PostgreSQL loss and runner restart.

## Injected versus physical faults

Disk-full and corruption tests inject database exceptions at controlled
transaction points. They prove application rollback/fail-closed behavior, not
the behavior of every filesystem or damaged SQLite page. Network faults are real
TCP proxy changes within the isolated Compose network. API and runner scenarios
use real process/container lifecycle operations.

The [failure matrix](failure-matrix.md) states the expected response for each
fault. The [verification report](verification.md) distinguishes test definitions
from checks actually executed on the development machine.

## Benchmarks

Core plus SQLite:

```powershell
.\.venv\Scripts\python -m scripts.benchmark_storage --count 10000 --batch 100
```

For PostgreSQL, run the script inside an API container and pass that container's
`DATABASE_URL`. Use an unused worker ID (the script uses 999). Do not benchmark
while Test Lab faults are active.

Always report:

- CPU, operating system, Python version and container/runtime context;
- record count, batch size, producer concurrency and worker count;
- database durability settings and whether a proxy/network was involved;
- elapsed time, IDs/second, duplicates, and p50/p95/p99 batch latency;
- sample count and warm-up method.

A batch latency is not a per-ID latency. Ten batches are insufficient for a
meaningful p99, even if the script prints one.

## CI behavior

GitHub Actions tests Python 3.11, 3.12, and 3.13, then builds the isolated
Compose acceptance stack, runs integration and browser suites, exercises lab,
watchdog, restore, and failure scripts, uploads browser artifacts, and tears down
containers. CI configuration is source code; a workflow file does not prove a
hosted run occurred. The verification report only claims observed executions.

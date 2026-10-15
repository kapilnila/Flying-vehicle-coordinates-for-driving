# Development guide

## Code map

| Path | Responsibility |
|---|---|
| `spatial_id/core.py` | Validation, exact quantization, packing, decoding, in-process generator |
| `spatial_id/storage.py` | Pools, migrations, leases, transactions, records, history |
| `spatial_id/api.py` | FastAPI boundary, deadlines, auth, routes, metrics |
| `spatial_id/cli.py` | Generate, decode, benchmark and serve commands |
| `spatial_id/test_lab.py` | Bounded scenarios, proxy control, reports and watchdog |
| `spatial_id/admin.py` | Migrations, proxy initialization and SQLite backup |
| `frontend/src/main.tsx` | Dashboard screens and client state |
| `frontend/src/style.css` | Responsive presentation and component styles |
| `tests/` | Core, storage, HTTP, crash, coordinator and fault tests |
| `scripts/` | Reproducible benchmark and acceptance workflows |

`coordination.py`, `service.py`, and `metrics.py` retain the earlier lightweight
SQLite service surface and its compatibility tests. The production Compose
entry point uses `api.py` and `storage.py`.

## Set up a development environment

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e '.[dev,service,postgres]'
Set-Location frontend
npm ci
Set-Location ..
```

The package extras are intentionally separate:

- Core installation has no dependencies.
- `service` adds FastAPI, Uvicorn, HTTPX and Prometheus client.
- `postgres` adds Psycopg, binary drivers and the connection pool.
- `dev` adds Pytest.

## Run during development

Backend with SQLite:

```powershell
.\.venv\Scripts\spatial-id serve --worker 21 --state .spatial-id\dev.db
```

Frontend in another terminal:

```powershell
Set-Location frontend
npm run dev
```

The frontend keeps 128-bit IDs as strings. Do not convert IDs or decoded decimal
values to JavaScript `number`; it cannot represent all 128-bit integers exactly.

## Design rules worth preserving

### Validation belongs near packing

Library and service code share the core validation and quantization path. New
input paths must not reimplement ranges with subtly different boundary behavior.
Boolean values must not pass as integers, and all floating inputs must be finite.

### Never manufacture time

If the wall clock moves behind durable state, generation fails. If a millisecond
exhausts its 1,024 sequences, the generator waits for actual time to advance.
Do not increment a logical timestamp just to preserve availability unless the ID
contract and documentation are explicitly revised.

### Treat timeouts as uncertain outcomes

An async request timeout does not cancel a database thread that may already be
committing. The correct client recovery is always the original idempotency key
and payload. Do not automatically allocate a replacement batch.

### Preserve atomicity

Allocation state, records, and request result must remain one transaction. A
change that commits any of them independently can create duplicate IDs, missing
records, or retries that cannot be resolved.

### Do not silently switch storage backends

PostgreSQL failure must be visible. Falling back to an unrelated SQLite file
would create a split-brain worker namespace and destroy the uniqueness claim.

## Adding an encoded field or version

Changing widths or meanings is a format-version change, not a schema-only edit.

1. Write the bit budget and compatibility behavior first.
2. Keep V1 decoding available.
3. Add a version-specific encoder/decoder path.
4. Add mask, shift, endpoint, malformed-version, and round-trip tests.
5. Update the API and dashboard diagram.
6. Decide how storage and exports expose both versions.
7. Rerun cross-version ordering and uniqueness tests.

Never reinterpret existing version-1 bits in place.

## Adding a migration

1. Make migration idempotent.
2. Serialize PostgreSQL migrations with the existing advisory lock.
3. Increment `schema_version` only after the new schema is usable.
4. Reject future versions rather than attempting downgrade behavior.
5. Test fresh install, upgrade, repeated migration, backup and restore.
6. State whether old records can be reconstructed; never invent missing data.

## Frontend behavior

Every screen needs explicit loading, validation, empty, disconnected, and retry
states. Health must distinguish unavailable metrics from numeric zero. Generation
retries reuse the current key only while normalized inputs are unchanged; New
request clears it. API and Test Lab tokens remain in React memory and are not
written to local storage.

## Pre-commit verification

```powershell
.\.venv\Scripts\python -m compileall -q spatial_id scripts tests
.\.venv\Scripts\python -m pytest
Set-Location frontend
npm run build
npm audit
Set-Location ..
```

Changes involving storage, retries, leases, Nginx, or the browser should also run
the relevant Docker suites from [Testing strategy](testing.md).

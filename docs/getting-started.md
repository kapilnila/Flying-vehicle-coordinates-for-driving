# Getting started

This guide covers the three supported ways to use the project: the complete
PostgreSQL application, standalone SQLite, and the Python library/CLI.

## Choose a mode

| Mode | Best for | Components | Persistence |
|---|---|---|---|
| Networked | Full demonstration and replica testing | React, Nginx, 2 APIs, PostgreSQL | Docker volume |
| Test overlay | Failure injection and recovery observation | Networked mode + Toxiproxy, runner, watchdog | Database + report volumes |
| Standalone | Small local installation | React, Nginx, 1 API, SQLite | Docker volume |
| Library/CLI | Embedding, algorithm tests and benchmarks | Python only | None unless using `serve` |

Do not run networked and standalone stacks on the same host port. Set different
`WEB_PORT` values or stop one first.

## Requirements

### Docker path

- Docker Desktop or Docker Engine with Compose v2.
- Internet access for the first image and dependency download.
- A current browser.
- Approximately 4 GB free memory for the full test stack.

Confirm the tools:

```powershell
docker version
docker compose version
```

### Local Python path

- Python 3.11 or newer.
- Node.js 22 or a compatible current LTS release for frontend development.
- PostgreSQL client access only when connecting outside Compose.

```powershell
python --version
node --version
npm --version
```

## Environment variables

Copy `.env.example` to `.env` for local Compose configuration. `.env` is ignored
by Git and must not be committed.

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_PASSWORD` | `local-test-password` | PostgreSQL application password |
| `WEB_PORT` | `8080` | Loopback host port for Nginx |
| `API_TOKEN` | empty | Optional bearer token for data and stats routes |
| `TEST_TOKEN` | required by test overlay | Separate bearer token for Test Lab controls |

The defaults are development conveniences. Change passwords and set both tokens
for any shared machine. The default stack publishes only `127.0.0.1:WEB_PORT`.

## Start the networked application

```powershell
docker compose up -d --build --wait
```

Compose performs these steps:

1. Starts PostgreSQL and waits for `pg_isready`.
2. Runs the versioned migration job.
3. Starts two APIs with worker IDs 0 and 1.
4. Waits for both APIs to hold valid leases and reach readiness.
5. Starts Nginx and serves the compiled React dashboard.

Open `http://localhost:8080`. Generate one event, click its ID to decode it,
then search the same vehicle on the History screen.

Useful checks:

```powershell
docker compose ps
Invoke-RestMethod http://localhost:8080/livez
Invoke-RestMethod http://localhost:8080/readyz
Invoke-WebRequest http://localhost:8080/metrics
```

If `API_TOKEN` is set, `/livez`, `/readyz`, `/healthz`, and `/metrics` remain
available without it; data routes and `/stats` require:

```powershell
$headers = @{ Authorization = 'Bearer your-api-token' }
Invoke-RestMethod http://localhost:8080/stats -Headers $headers
```

## Start the testing overlay

Use an isolated project name so test faults cannot be confused with a normal
development stack:

```powershell
$env:WEB_PORT = '18081'
$env:TEST_TOKEN = 'choose-a-local-test-token'
docker compose -p spatial-id-acceptance `
  -f compose.yaml -f compose.test.yaml `
  up -d --build --wait
```

Open `http://localhost:18081`, select Test Lab, and enter the token. The available
scenarios are concurrent load, database latency, and database disconnect.

Run only one fault scenario at a time. The runner caps duration, concurrency,
batch size, requested IDs, and fault time. A separate watchdog reads the fault
expiry from the report volume and restores connectivity if the runner stops.

## Start standalone SQLite

```powershell
$env:WEB_PORT = '18082'
docker compose -p spatial-id-sqlite `
  -f compose.local.yaml up -d --build
```

Open `http://localhost:18082`. The second backend appears unavailable by design,
and Test Lab explains that the PostgreSQL testing configuration is required.

SQLite is configured with WAL journaling, full synchronization, foreign keys,
and a bounded connection pool. Writes still serialize because SQLite has one
writer at a time.

## Install the Python package

Core only:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e .
```

Development, API, and PostgreSQL dependencies:

```powershell
.\.venv\Scripts\python -m pip install -e '.[dev,service,postgres]'
```

Generate and decode:

```powershell
.\.venv\Scripts\spatial-id generate --worker 7 --lat 12.9716 --lon 77.5946 --altitude 120 --speed 18
.\.venv\Scripts\spatial-id decode 0123456789abcdef0123456789abcdef
```

Run the local SQLite API directly:

```powershell
.\.venv\Scripts\spatial-id serve `
  --worker 7 `
  --state .spatial-id\state.db `
  --host 127.0.0.1 `
  --port 8080
```

The server defaults to a 30-second lease, maximum batch 1,000, five-second
request deadline, 64 active requests, and ten-second graceful shutdown.

## Frontend development

Start an API on port 8080, then in a second terminal:

```powershell
Set-Location frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. Vite proxies data routes to port 8080. The second
Health node expects port 8081 in development and may show unavailable.

## Common startup problems

### Port already in use

Choose a different gateway port:

```powershell
$env:WEB_PORT = '18081'
docker compose up -d
```

### Test overlay refuses to interpolate

`TEST_TOKEN` is intentionally required. Set a nonempty value in the current
terminal or `.env`, then rerun Compose.

### API starts but is not ready

Read logs and confirm PostgreSQL/Toxiproxy health:

```powershell
docker compose ps
docker compose logs --tail 200 api-0 api-1 db migrate
```

After a database interruption, an API can remain unready until its old lease
expires and it safely reacquires ownership. Do not delete allocation rows.

### Worker already leased

Each active generator needs a distinct worker ID. Stop the old owner and wait
for lease expiry, or assign a genuinely unused ID. Never bypass the check by
manually editing the database.

### Resetting a disposable environment

`docker compose down` preserves volumes. Removing volumes destroys database
history. Only use the following for a project you explicitly intend to erase:

```powershell
docker compose -p your-disposable-project down -v
```

For normal recovery, follow [Operations and recovery](operations.md).

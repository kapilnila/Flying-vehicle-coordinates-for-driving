# Operations and recovery

## Start/stop

`docker compose up -d --build --wait` starts the networked application.
`docker compose down` stops its containers without deleting volumes.
Use `docker compose logs api-0 api-1 migrate` to diagnose startup problems.
The Test Lab uses the additional `compose.test.yaml` file and requires TEST_TOKEN.

To return from testing to ordinary network mode, run `docker compose -f
compose.yaml -f compose.test.yaml down` and then start the default configuration.
Keep volumes; do not use `down -v` for data you want to retain.

## Backups

SQLite: stop or keep using the service while taking a consistent SQLite backup:

```powershell
python -m spatial_id.admin backup --database-url sqlite:///.spatial-id/state.db --output backup.db
```

The destination must not exist. The backup API includes committed WAL state;
copying just the active `.db` file does not. PostgreSQL: use `pg_dump -Fc` against
the primary, store dumps outside the database volume, and test restoration to a
separate database using `pg_restore`.

Never restore into a running generator. Stop every instance and revoke all old
workers. A stale backup may omit acknowledged allocations; before re-enabling
workers, prove the generation clock is later than every timestamp emitted since
the backup. If that bound cannot be established, retain the restored service
read-only and do not claim collision-free generation. Never reset allocation
rows to overcome a clock error. Document backup retention and recovery point.

## Migrations

The Compose migration job completes before APIs start. Schema versions are
recorded in `schema_version`; unknown future versions are rejected. Stop legacy
generators before migration. Their last timestamp is migrated with sequence 1023
so the old millisecond cannot be reused. Existing generated IDs stay decodable,
but no historical records exist for IDs issued before history was added.

## Recovery

On database disconnect, requests fail within bounded deadlines. Heartbeat loss
invalidates readiness. The worker can reacquire only after the old lease expires
(default 30 seconds), then continues from persisted allocation state. Retries
must preserve their Idempotency-Key. A failed transaction never becomes a
partial successful batch. A timed-out client should regard the outcome as
unknown until an idempotent retry resolves it.

Automatic container restart uses process liveness. Readiness can remain false
while the process waits safely for lease expiry; this alone does not trigger
data resets. API shutdown allows ten seconds for draining.

Test faults persist their expiry before activation. Both the runner and separate
watchdog restore the proxy; cleanup retries if the proxy is itself unavailable.
Reports are stored in an independent SQLite volume. Test processes cannot access
the Docker socket. The host failure script uses only its fixed test project.

## Readiness and monitoring

| Endpoint | Healthy meaning | Typical use |
|---|---|---|
| `/livez` | Event loop can serve a response | Process liveness/restart |
| `/readyz` | Database works and this worker holds a valid lease | Load-balancer readiness |
| `/healthz` | Alias of readiness | Compatibility |
| `/metrics` | Prometheus text can be rendered | Scraping |
| `/stats` | Process counters and recent latency are available | Dashboard |

Liveness does not imply safe generation. Remove an instance from traffic when
readiness fails, but leave it alive long enough to reacquire its lease after
temporary database recovery.

Suggested alerts for a real deployment include:

- no ready API replicas;
- increasing `storage_unavailable`, `worker_lease`, or `clock_regression` errors;
- any duplicate-ID assertion in an independent consumer audit;
- p95/p99 commit latency beyond the request budget;
- connection-pool exhaustion or sustained transaction retries;
- sequence waits approaching workload limits;
- database disk/WAL growth and backup age;
- worker fence changes outside expected deployment events.

Unavailable metrics are unknown, not zero. The dashboard makes this distinction.

## Incident runbooks

### Database unavailable

1. Stop automated retries that do not preserve idempotency keys.
2. Confirm `/livez` versus `/readyz` to separate process and storage failure.
3. Inspect PostgreSQL, network/proxy, pool, and API logs.
4. Restore connectivity without changing allocation rows or storage backend.
5. Allow old leases to expire and instances to reacquire safely.
6. Retry uncertain requests with their original keys and payloads.
7. Verify a new record and its history before restoring normal traffic.

### Clock regression

1. Remove the affected worker from traffic.
2. Compare system time with the persisted allocation timestamp.
3. Correct the host/container time source; do not edit the timestamp backward.
4. Wait until real time is at or beyond the durable high-water mark.
5. Restart or let the worker reacquire, then verify readiness and generation.

### Worker lease conflict

1. Identify every process configured with that worker ID.
2. Stop duplicate owners; do not reuse a worker across simultaneous processes.
3. Wait for database lease expiry.
4. Start exactly one owner and confirm it receives a higher fence.

### Disk full

1. Stop write traffic before the database becomes inconsistent or unavailable.
2. Recover space using database/storage procedures, not by deleting allocation rows.
3. Check PostgreSQL/SQLite integrity and WAL state.
4. Resolve uncertain requests through idempotency replay.
5. Confirm acknowledged IDs still have records.

### Test fault left active

The independent watchdog normally restores expired faults. Check the watchdog,
runner, and Toxiproxy containers. Restart the watchdog or runner through Compose;
startup reconciliation restores normal proxy state. Keep Toxiproxy administration
internal and do not manually delete application database state.

## Capacity and retention

Each worker can emit at most 1,024 IDs in one actual millisecond. A batch that
does not fit waits outside its lock for the next millisecond, for at most 100 ms.
Use distinct workers to scale PostgreSQL generation horizontally. SQLite remains
single-writer even with multiple connections.

Records and idempotency results never expire in V1. Track table/index size,
backup duration, restore duration, and export cost. Define an archival policy
before sustained ingestion, including how long clients may retry old keys.

## Shutdown and upgrades

Uvicorn receives a ten-second graceful-shutdown window and Compose allows 15
seconds before force. During rolling change:

1. Keep at least one other ready worker.
2. Stop routing new requests to the target.
3. Allow active requests to drain.
4. Stop the process and wait for lease release/expiry as required.
5. Run migrations once.
6. Start the replacement with the intended worker ID.
7. Require readiness before restoring traffic.

Do not run incompatible application versions against a schema whose semantics
they do not share. The current migrator rejects unknown future schema versions.

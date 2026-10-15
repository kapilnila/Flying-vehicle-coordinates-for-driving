# Interview guide

## Two-minute explanation

This project is a Snowflake-inspired 128-bit ID service for spatial events. A
timestamp in the most significant bits makes IDs approximately time sortable.
Coordinates, altitude, speed, and vehicle class are quantized into fixed-width
fields, while worker and sequence bits provide distributed uniqueness. The
generator is lock-protected within a process. The service also leases worker
IDs and atomically commits sequence allocation, original records, and retry
results in PostgreSQL or SQLite so crashes cannot reproduce acknowledged IDs.

## Questions to be ready for

1. **Why not use UUIDv4?** UUIDv4 does not provide time order or embedded,
   locally decodable routing metadata.
2. **What guarantees uniqueness?** The timestamp/worker/sequence tuple, given
   exclusive worker leases and the persisted restart barrier.
3. **Why are coordinates lossy?** Raw floating point does not fit predictably;
   fixed cells make range, precision, and bit cost explicit.
4. **What if time goes backward?** Fail closed and alert. Logical time would
   improve availability but make encoded timestamps temporarily inaccurate.
5. **Where is the bottleneck?** The worker-row lock and durable transaction per
   batch. Batching amortizes synchronization and network overhead; distinct
   workers allow parallel PostgreSQL allocations. SQLite serializes writers.
6. **How would this become multi-region?** Use a linearizable distributed lease
   backend, assign disjoint worker ranges per region, monitor clock skew, and
   route only to instances with healthy leases.
7. **Why not store precise location in the ID?** IDs are immutable identifiers,
   not mutable business records or safety-grade telemetry.

## Honest limitations

- PostgreSQL is a single primary; automatic failover and replication are not included.
- Textual IDs take more space than raw 16-byte values.
- Location precision varies east/west with latitude.
- IDs reveal coarse time and location, which may be inappropriate for public
  identifiers without encryption or a separate opaque external ID.
- Optional bearer-token authentication and bounded concurrency are included.
  Public deployment still needs TLS, per-user authorization, rate limits, and
  secrets management. Default Compose bindings are loopback-only.

## Resume bullet

> Built a restart-safe 128-bit distributed spatial ID service in Python,
> supporting 1,024 workers and 1,024 IDs/worker/ms; implemented deterministic
> bit packing/decoding, worker leases, crash-safe timestamp barriers, concurrent
> tests, HTTP APIs, Prometheus metrics, Docker deployment, and CI.


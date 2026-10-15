# API contract

All IDs are canonical 32-character lowercase hexadecimal strings. Decoded
128-bit decimal values are strings in HTTP JSON. Server errors have
`{"error":{"code":"...","message":"..."}}` form.

The examples assume `http://localhost:8080`. When `API_TOKEN` is configured,
send `Authorization: Bearer <token>` on every route except `/livez`, `/readyz`,
`/healthz`, and `/metrics`. JSON responses include `Cache-Control: no-store`.

## Generate

`POST /v1/ids`, `Content-Type: application/json`, optional `Idempotency-Key`.

```json
{"latitude":12.9716,"longitude":77.5946,"altitude_m":120.4,"speed_mps":17.8,"vehicle_class":3,"vehicle_id":"VEH-1024","count":10}
```

The four spatial/speed fields are required numbers. Optional class defaults to
0, count to 1 (maximum 1000), vehicle to null, and capture time to receipt time.
`captured_at_ms` is a nonnegative integer Unix timestamp. Unknown fields, repeated
JSON keys, numeric strings, NaN/Infinity, booleans in numeric fields, and invalid
ranges are rejected. Body limit: 64 KiB.

First commit returns `201` with `ids`, `count`, `worker_id`, and `request_key`.
A matching retry returns `200`, the original body, and
`Idempotency-Replayed: true`. A conflicting payload for that key returns `409`.
Without a client-provided key, a server key is generated, but a client that loses
the response cannot safely deduplicate its next request. Always send a stable
key when retrying automatically. On timeout the commit may have succeeded;
resolve it by resending the exact same key and payload.

Example first response:

```json
{
  "ids": [
    "02e2587b2e0c9396b72da47811305400"
  ],
  "count": 1,
  "worker_id": 21,
  "request_key": "vehicle-42-sample-0001"
}
```

Response headers include `Idempotency-Replayed: false` for the first commit and
`Idempotency-Replayed: true` for a replay. The key must contain 1–128 ASCII
characters. A server-generated key is returned when the header is omitted.

### Normalization and retry identity

The request fingerprint covers all normalized supported fields. Numeric spatial
values normalize to floating point; omitted `vehicle_class`, `count`,
`vehicle_id`, and `captured_at_ms` normalize to their documented defaults.
Changing any input creates a different logical request and must use a new key.
Object member order and insignificant JSON whitespace do not change identity.

### Atomic batch semantics

One request produces one transaction. Either every ID and record is committed
with the stored response, or none are. The sequence range is contiguous for the
selected worker and encoded millisecond. If a batch cannot fit in the current
millisecond, the service releases database locks and waits up to 100 ms for real
time to advance. It does not split the batch or manufacture future time.

## Decode and records

| Route | Behavior |
|---|---|
| `GET /v1/ids/{id}` | Decode even without a stored record |
| `GET /v1/records/{id}` | Original data, capture/generation times, request association |
| `GET /v1/vehicles/{vehicle}/records` | `limit` (1–1000), `cursor`, `from_ms`, `to_ms` |
| `GET /v1/vehicles/{vehicle}/latest` | Greatest capture time, then ID |
| `GET /v1/vehicles/{vehicle}/export` | Streaming JSONL in capture-time/ID order |

History responses contain `items` and nullable `next_cursor`. Filters are
inclusive. Export is not a snapshot; download failures must be retried and
partial files discarded. Vehicle identifiers should be URL-encoded.

### Decode response

`GET /v1/ids/{id}` returns every encoded field. The decimal `value` is a string
over HTTP so JavaScript clients cannot lose precision.

```json
{
  "id": "02e2587b2e0c9396b72da47811305400",
  "value": "3833709692959327921067566179183252480",
  "timestamp_ms": 1792000368220,
  "timestamp_utc": "2026-10-14T17:52:48.220000Z",
  "version": 1,
  "latitude": {
    "bucket": 299926,
    "lower": 12.971420288085938,
    "upper": 12.971763610839844,
    "center": 12.97159194946289
  },
  "longitude": {
    "bucket": 750298,
    "lower": 77.59437561035156,
    "upper": 77.59471893310547,
    "center": 77.59454727172852
  },
  "altitude": {"bucket": 1144, "lower_m": 120, "upper_m": 121},
  "speed": {"bucket": 17, "lower_mps": 17, "upper_mps": 18},
  "vehicle_class": 3,
  "worker_id": 21,
  "sequence": 0
}
```

The example is illustrative. Its timestamp and ID came from a local development
run; generated values change with time.

### History pagination

```http
GET /v1/vehicles/VEH-42/records?limit=100&from_ms=1790000000000&to_ms=1800000000000
```

```json
{
  "items": [
    {
      "id": "02e2587b2e0c9396b72da47811305400",
      "vehicle_id": "VEH-42",
      "captured_at_ms": 1792000368000,
      "generated_at_ms": 1792000368220,
      "latitude": 12.9716,
      "longitude": 77.5946,
      "altitude_m": 120.4,
      "speed_mps": 17.8,
      "vehicle_class": 3,
      "worker_id": 21,
      "request_key": "vehicle-42-sample-0001"
    }
  ],
  "next_cursor": null
}
```

The default limit is 100 and the maximum is 1,000. Follow `next_cursor` exactly;
do not parse or modify it. Pagination is stable by `(captured_at_ms, id)`, but it
is not a database snapshot: concurrent late inserts can affect later pages.

## Operations

`/livez` checks process responsiveness; `/readyz` and `/healthz` verify database
and current worker ownership. `/metrics` is Prometheus text; `/stats` contains
dashboard counters and recent latency percentiles. Counters reset on restart.

Status codes include 400 validation, 401 authentication, 404 absent record,
409 conflicting retry, 413 size limit, 415 media type, and 503 transient
unavailability/overload/clock or lease failure. Generation failures are never
automatically redirected to another storage backend.

### Error codes

| HTTP | Code | Meaning and caller action |
|---:|---|---|
| 400 | `invalid_json` | Fix malformed, duplicate-key, or non-finite JSON |
| 400 | `validation_error` | Fix field type, range, cursor, ID, or query |
| 401 | `unauthorized` | Supply the configured bearer token |
| 404 | `not_found` | The record or vehicle history does not exist |
| 409 | `idempotency_conflict` | Use a new key for the changed payload |
| 413 | `request_too_large` | Reduce the request body below 64 KiB |
| 415 | `unsupported_media_type` | Send `Content-Type: application/json` |
| 500 | `internal_error` | Retry generation with the same key; inspect logs |
| 503 | `deadline_exceeded` | Outcome may be uncertain; retry exact key/payload |
| 503 | `overloaded` | Honor `Retry-After`, then retry exact key/payload |
| 503 | `storage_unavailable` | Preserve key, wait for database recovery, retry |
| 503 | `worker_lease` | Route away or wait for safe lease acquisition |
| 503 | `clock_regression` | Stop generation and repair/synchronize system time |
| 503 | `sequence_exhausted` | Reduce load/batch or wait for time to advance |

Transient `503` responses include `Retry-After: 1` when generated by the API
failure boundary. Clients should add jitter and must stay within their own retry
budget.

### Metrics

`/metrics` exposes:

| Metric | Type | Meaning |
|---|---|---|
| `spatial_id_requests_total` | Counter | Completed requests by route and status |
| `spatial_id_errors_total` | Counter | Structured failures by code |
| `spatial_id_generated_total` | Counter | IDs committed by this process, excluding replay |
| `spatial_id_replays_total` | Counter | Idempotency replays served |
| `spatial_id_request_seconds` | Histogram | End-to-end request duration |
| `spatial_id_commit_seconds` | Histogram | Allocation and durable commit duration |
| `spatial_id_storage_retries` | Gauge | Transaction retries in this process |
| `spatial_id_sequence_waits` | Gauge | Waits caused by sequence capacity |

Process metrics reset when the API restarts. Durable record counts live in the
database; do not infer total history solely from process counters.

## Test runner

`POST /test-api/runs` accepts scenario (`load`, `database_latency`,
`database_disconnect`), duration (1–60 seconds), concurrency (1–32), batch
(1–1000), total_ids (1–100000), and fault_seconds (1–10). One run at a time.
HTTP calls already in flight can take up to two seconds beyond the load window.
`GET /test-api/runs` lists recent reports; `GET /test-api/runs/{id}` polls;
`POST /test-api/runs/{id}/cancel` cancels and restores connectivity;
`GET /test-api/runs/{id}/report` downloads the report. All require the separate
test bearer token. Targets and commands cannot be supplied by the caller.

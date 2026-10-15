# Design decisions

This page records the major tradeoffs behind the current implementation. It is
not a substitute for code or API contracts; it explains why those contracts look
the way they do.

## Custom 128-bit ID instead of UUIDv4

UUIDv4 provides excellent decentralized uniqueness but no useful time order or
embedded spatial metadata. The project intentionally trades opacity and standard
tooling for deterministic local decoding and millisecond ordering.

Consequence: IDs expose coarse time and position, and every format evolution must
respect a fixed bit budget. Public systems may need a separate opaque identifier.

## Generation time, not capture time, controls uniqueness

Capture time comes from the event source and can be delayed, duplicated, wrong,
or adversarial. Using it in the timestamp field would let late data collide with
old allocation ranges. Server generation time is therefore encoded; capture time
is stored separately and drives history ordering.

## Floor-based fixed cells

Fixed-width buckets make storage cost and maximum error explicit. Floor avoids
round-to-nearest ambiguity, and the decoder honestly returns a cell rather than a
fake reconstruction. Exact float integer ratios protect adjacent boundaries from
intermediate floating-point rounding.

Alternative geospatial curves or indexes could improve locality, but they would
change the format and are unnecessary for the V1 goal of embedding approximate
metadata.

## Fail on clock rollback

Using a logical future timestamp would improve availability but make IDs claim a
time that did not occur. V1 chooses semantic honesty and uniqueness safety:
generation fails until real time catches the durable high-water mark.

## Wait instead of overflowing sequence

The 10-bit sequence allows 1,024 IDs per worker per millisecond. On exhaustion,
the service releases locks and waits briefly for actual time to advance. It does
not wrap, split an atomic batch, or invent a timestamp.

Horizontal throughput comes from distinct worker IDs; batch throughput comes from
amortizing a durable commit.

## Lease plus fencing token

An expiry alone cannot stop a paused process from waking after another owner has
taken over. Every acquisition increments a fence. Generation checks owner, fence,
and expiry inside the same transaction as allocation and records, so stale owners
cannot commit.

Database time governs shared expiry because process clocks may disagree.
Monotonic process time governs local deadlines because wall-clock adjustments
should not extend a timeout.

## Database-backed allocation

PostgreSQL is already the durable shared coordination point. A worker-row lock
and per-idempotency-key advisory lock provide the needed serialization without
adding a separate consensus service.

The tradeoff is write latency and dependence on one database primary. A larger
multi-region system would need a deliberately designed linearizable allocator or
disjoint regional worker namespace, not an automatic local fallback.

## Atomic records and idempotency result

Returning an ID before saving its record would violate the acknowledged-history
guarantee. Saving a key separately could create a response without records or
records without a replay result. V1 commits allocation, immutable records, and
the exact response together.

## SQLite as a separate operating mode

SQLite gives a dependency-light local service and exercises the same transaction
contract. It is not an emergency fallback for PostgreSQL. Automatic switching
would split worker state between unrelated databases and make uniqueness claims
unsafe.

## Cursor pagination by capture time and ID

Offset pagination becomes expensive and unstable as history grows. The tuple
`(captured_at_ms, id)` provides deterministic order even for equal capture times.
The cursor is opaque and pages are not snapshots under concurrent insertion.

## Test Lab as a separate service

Failure controls should not live in the production API process. The runner has a
separate token, fixed targets, strict limits, independent report storage, and no
Docker socket. A separate watchdog can restore network state even if the runner
is unavailable.

## Nginx does not retry generation automatically

Blind proxy retry after a lost upstream response can duplicate a non-idempotent
request. Nginx disables upstream retry for the API. The caller owns retry intent
and reuses a stable `Idempotency-Key`, allowing either replica to resolve it.

## Indefinite retention in V1

Keeping request results forever makes retry semantics clear for a portfolio
system and avoids inventing an expiry contract. It is not a sustainable default
for unbounded production ingestion. A future retention design must jointly
address privacy, record history, and idempotency windows.

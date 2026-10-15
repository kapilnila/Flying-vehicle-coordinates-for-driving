# Contributing

Thank you for improving the Distributed Spatial ID System. Correctness changes
can affect global uniqueness and durable history, so contributions should make
their invariants and failure behavior explicit.

## Before changing code

Read the documents related to the change:

- [Architecture](docs/architecture.md) for ID and transaction invariants.
- [API contract](docs/api.md) for externally visible behavior.
- [Data model](docs/data-model.md) for storage changes.
- [Testing strategy](docs/testing.md) for required evidence.
- [Security model](docs/security.md) for new trust boundaries or data exposure.

For a bug, describe the smallest reproducible case and the violated invariant.
For a feature, state compatibility, migration, rollback, observability, and
failure behavior before implementation.

## Development setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e '.[dev,service,postgres]'
Set-Location frontend
npm ci
Set-Location ..
```

## Change expectations

- Keep the core package dependency-free.
- Preserve canonical 32-character lowercase hexadecimal output.
- Do not reinterpret V1 bits; introduce an explicit format version.
- Share validation and quantization instead of copying rules between layers.
- Keep allocation, records, and idempotency results in one transaction.
- Never silently fail over to another independent database.
- Keep client-visible IDs as strings.
- Return structured errors without internal exception details.
- Add bounded behavior for new waits, pools, queues, retries, and test actions.
- Document operational steps and honest limitations.

## Tests

At minimum:

```powershell
.\.venv\Scripts\python -m compileall -q spatial_id scripts tests
.\.venv\Scripts\python -m pytest
Set-Location frontend
npm run build
Set-Location ..
```

Run real Docker tests for changes involving PostgreSQL, replicas, Nginx,
idempotency, leases, shutdown, Test Lab, or frontend behavior. See
[Testing strategy](docs/testing.md) for commands and isolation rules.

New format or numeric logic needs minimum, maximum, just-inside, just-outside,
non-finite, boolean, and adjacent floating-boundary cases. New storage behavior
needs rollback, restart, concurrent retry, and uncertain-outcome coverage.

## Documentation

Update contracts in the same change as code. The root README should remain a
readable project overview; put detailed procedures in `docs/`. Do not replace
measured results with estimates. Date new verification evidence and describe the
environment, durability settings, sample size, and limitations.

## Commit and review guidance

Keep changes focused. A reviewer should be able to answer:

1. Which invariant or user behavior changed?
2. Can old IDs and records still be interpreted?
3. What happens on timeout, crash, rollback, retry, and restart?
4. Which tests demonstrate the claim?
5. Is operator action or migration required?
6. Did the security or privacy boundary expand?

Never include `.env`, database volumes, secrets, browser traces containing real
data, or generated backup files in a commit.

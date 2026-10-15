# Documentation index

This directory is the technical handbook for the Distributed Spatial ID System.
The root [README](../README.md) is the project overview and fastest demo path.

## Read by role

### Evaluator or interviewer

1. [Project README](../README.md) — problem, architecture, quick start and evidence.
2. [Architecture and guarantees](architecture.md) — correctness invariants and tradeoffs.
3. [Design decisions](design-decisions.md) — alternatives and explicit tradeoffs.
4. [Verification and benchmarks](verification.md) — what was actually executed.
5. [Interview guide](interview-guide.md) — design questions and concise explanations.

### Application developer

1. [Getting started](getting-started.md) — install and run locally.
2. [API contract](api.md) — inputs, outputs, retries, errors and pagination.
3. [Development guide](development.md) — code layout and change workflow.
4. [Data model](data-model.md) — persisted tables and event lifecycle.

### Operator or reviewer

1. [Operations and recovery](operations.md) — deployment, backup and incidents.
2. [Security model](security.md) — trust boundaries and production gaps.
3. [Failure matrix](failure-matrix.md) — expected behavior under faults.
4. [Testing strategy](testing.md) — suites, isolation rules and reproduction.

## Source of truth

| Topic | Primary document |
|---|---|
| Bit allocation and uniqueness invariant | [architecture.md](architecture.md) |
| Major tradeoffs and rejected alternatives | [design-decisions.md](design-decisions.md) |
| HTTP request/response behavior | [api.md](api.md) |
| Schema and record lifecycle | [data-model.md](data-model.md) |
| Environment and first run | [getting-started.md](getting-started.md) |
| Backup, restore and incident handling | [operations.md](operations.md) |
| Threat model and deployment hardening | [security.md](security.md) |
| Test intent and commands | [testing.md](testing.md) |
| Executed results | [verification.md](verification.md) |

Measured results are dated evidence, not permanent guarantees. When code or
deployment settings change, update the relevant contract document first, then
rerun verification and append new evidence without erasing useful history.

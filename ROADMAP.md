# Roadmap

The current release is a tested single-primary reference implementation. The
roadmap separates changes that deepen this project from features intentionally
outside the spatial-ID scope.

## Next: repository and delivery maturity

- Add an explicit MIT `LICENSE` with the chosen copyright holder.
- Add formatting, linting, type checking, Markdown link checks, dependency
  review, SBOM generation, and container-image scanning to CI.
- Publish immutable container tags and package artifacts with provenance.
- Add OpenAPI snapshot/compatibility checks and automated migration tests.
- Add a longer benchmark profile with warm-up and machine-readable reports.

## Next: production-facing security

- Terminate TLS and document trusted proxy behavior.
- Replace shared bearer tokens with OIDC/OAuth identities and roles.
- Add per-principal quotas, audit events, token/credential rotation, and managed
  secrets.
- Define privacy retention/deletion behavior for precise vehicle event history.
- Split migration, runtime, backup, and monitoring database roles.

## Next: operational resilience

- Add PostgreSQL replication and controlled failover testing.
- Define recovery-point and recovery-time objectives.
- Automate encrypted backups and scheduled restore rehearsals.
- Add dashboards/alerts for lease loss, clock regression, saturation, commit
  latency, retry rate, storage growth, and backup age.
- Add explicit maintenance/read-only mode for controlled recovery.

## Later: scale and lifecycle

- Add configurable archival while preserving a documented idempotency window.
- Partition large history tables and evaluate index/write amplification.
- Benchmark more workers, producers, batch sizes, and network distances.
- Evaluate regional worker partitions or a linearizable global lease service.
- Add client SDKs that enforce string IDs and correct idempotent retries.

## Intentionally outside this project

- Vehicle identity/authentication as a business domain.
- Maps, route planning, trajectories, previous/next event links.
- Collision detection, flight control, navigation, or safety decisions.
- Precise position recovery from the lossy ID.
- 3D velocity vectors, direction, or mutable telemetry inside the identifier.
- Silent offline synchronization or automatic switching between independent
  storage backends.

Adding these would dilute the central systems problem. They belong in services
that consume IDs and records, not in the identifier format itself.

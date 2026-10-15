# Failure coverage

| Scenario | Expected response | Verification |
|---|---|---|
| Invalid range, non-finite or duplicate JSON fields | 400, no record | Core/API tests |
| Request body too large | 413 | API/live HTTP tests |
| Client disconnect before response | Resolve original key, one atomic batch | Live HTTP socket-disconnect test |
| Deadline before eventual commit | 503 then replay original response | API deadline/uncertain-commit test |
| Overloaded process or unexpected exception | Bounded structured response, no diagnostic leakage | API overload/error tests |
| Same key, same request across replicas | Original IDs, one batch | Storage + network tests |
| Same key, changed payload | 409 | Storage tests |
| Crash before commit | No partial records | Abrupt subprocess exit test |
| Crash after commit before response | Retry retrieves original result | Abrupt subprocess exit test |
| Worker already owned | Startup fails | Lease tests |
| Expired or replaced fence | No new commit by stale owner | Storage tests |
| Clock rollback/frozen clock | Bounded 503, no unsafe ID | Core/storage tests |
| Disk full mid-transaction | Rollback whole batch | Injected SQLite error test |
| Database corruption | Fail closed | Injected storage error test |
| Database disconnect/latency | Bounded errors; restore and retry | Test Lab + proxy tests |
| API process kill/database restart | Safe restart, history remains | Host failure script |
| Runner unavailable during fault | Expiry watchdog restores network | Unit test plus live paused-runner scenario |
| Late-arriving vehicle sample | Immutable record, deterministic query | History tests |
| Legacy state | Old timestamp is treated as consumed | Migration tests |
| Backup restore | Records readable; generation requires safe time barrier | SQLite backup test + real PostgreSQL dump/restore |
| Entire volume loss | Operator restore required | Documented limitation |

Read `verification.md` for which checks were actually executed in the development
environment. Test definitions alone are not evidence of a passing deployment.

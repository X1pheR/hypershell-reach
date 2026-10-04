# Unified peer-local persistence

## Contract

Version 0.11 introduces an opt-in `workspace.database` path. The database is local to one Reach peer and is the sole operational writer for Runs, Tasks, Task leases and configured Candidates. Legacy configurations remain supported. A configured missing or unsupported database MUST fail closed; it MUST NOT silently return to old files.

| Requirement | Normative behavior |
|---|---|
| UP-01 | Preserve record IDs, historical payload schemas, revision values and explicit retained/ambiguous state. |
| UP-02 | Use one transaction for a Task read/modify/write, revision CAS, equivalent-create check, archive change and associated lease removal. |
| UP-03 | Acquire/refresh/release leases transactionally without advancing Task revision. Expiry neither reconciles mutations nor replays work. |
| UP-04 | Persist the server-owned pending-mutation blocker before dispatch. Arbitrary continuity patching MUST NOT erase it; explicit postcondition reconciliation is required. Neither completed nor cancelled closure may bypass pending state. |
| UP-05 | Candidate authority is explicit configuration, independent of table existence. An unconfigured peer MUST deny Candidate mutation. No replication or writer election is introduced. |
| UP-06 | Preserve Run restart/ownership-loss rules for synchronous and asynchronous execution. Age alone MUST NOT classify a running execution as stale. |
| UP-07 | Run cleanup considers only eligible terminal, unambiguous, unretained records ended before cutoff. Task cleanup considers only terminal archived unretained records older than cutoff. No automatic Candidate deletion. |
| UP-08 | MCP, HTTP API and UI use the same sanitized product read model. Browser state remains read-only; database access is server-side. |
| UP-09 | Migration validates all sources before atomic installation, preserves originals, rejects collisions/malformed state and records counts plus deterministic semantic digests. |
| UP-10 | Rollback exports current database state, including post-migration writes, into the previously supported stores. Old preimages alone are not a valid rollback. |
| UP-11 | Concurrent operations, busy writers and process interruption MUST produce either committed state or explicit failure, never silent partial state. |

## Design

The standard-library SQLite driver avoids another service or dependency. Typed indexed columns serve operational filters and counts; validated JSON payloads preserve full contracts. Schema version 2 uses `runs`, `tasks`, `task_leases`, `candidates` and `metadata`. Run and Candidate Task references remain soft links because independently retained historical records may outlive Tasks. Lease rows belong to Tasks and use foreign keys.

A canonical-path transaction context shares the same connection for nested synchronous store calls. Write transactions use `BEGIN IMMEDIATE`, foreign keys, a five-second busy timeout and full synchronous durability. WAL permits readers during writes. Transactions never contain awaits or remote execution. SQLite's ordinary bounded checkpoint policy applies; ordinary requests never run full VACUUM. Free pages are reused. Operators may perform offline checkpoint/maintenance after measuring growth; no extra daemon or scheduler is required.

The simpler alternative—retaining YAML plus locks and adding another index—would preserve two authorities and require index repair. Extending the existing Run SQLite implementation centralizes atomicity without adding distributed machinery. No general event-sourcing framework is needed: current Task state plus linked Runs provides the operational activity view; this is not an audit history of every Task revision.

## Migration and recovery

Migration is an explicit offline operation. Stop the old serving peer using an independent recovery path, preserve the old image/configuration and all stores, then stage the new database from the v0.10 Run database and Task/Candidate files. Validate IDs, schemas, revisions, timezone-aware timestamps, duplicate active/archive identity and leases. Schema-v1 Task revision zero is accepted where its historical contract allows it. Valid historical terminal Tasks in the archive may have a null archive timestamp: preserve physical archive membership and the unchanged payload, identify them in the migration receipt, and exclude them from age-based retention. Non-null malformed timestamps remain fatal.

The complete staged database must pass integrity, counts, semantic digest and index/payload invariants before exclusive atomic publication. A partial stage is not accepted authority. Repeated migration must either prove the same accepted migration identity or fail visibly. Source files remain unchanged.

Use the new release's current-state export before an old-release rollback. Export into a new destination, verify its receipt and semantic parity, then configure the old release against that export while all writers remain stopped. Never start the old release against stale pre-migration YAML or JSON. A later upgrade must import the latest exported/old-writer state anew.

Keep migration preimages until deployment-owned acceptance and retention disposition. Compact archival is an operator action after both peers pass acceptance and rollback is proven.

## Compatibility and security

No raw commands, script bodies, argument values, environment values, stdout or stderr content are added to persistence. Existing sanitized payload boundaries remain authoritative. Metadata and database paths contain no credentials. Public interfaces preserve existing fields; bounded filter/pagination additions are additive.

Cancellation with unresolved pending-mutation state is deliberately tightened: cancellation cannot bypass the same reconciliation requirement as completion. This prevents terminal state from concealing an ambiguous mutation.

Database files and WAL sidecars require a writable local directory owned by the service identity. Never place them on a network filesystem or share them between peers. Backups must use an application-consistent SQLite backup/export or a stopped writer; copying only the live main file while WAL exists is insufficient.

## Acceptance

Tests must cover existing v0.10 migration, fresh state, invalid/colliding source, atomic failure/rerun, rollback export/restore, revision CAS, leases, pending receipts, Candidate lifecycle/authority, protected retention, process kill/WAL recovery and concurrent readers/writers. Run the full frozen test suite and repository browser/build gates.

Benchmark copies of representative production-scale data. Separate database/store initialization, isolated process startup and real host boot; isolated measurements do not establish host cold-boot performance. Measure recent/filter queries, Tasks, archive, related Runs, counts, Candidates and cleanup. Production acceptance additionally checks exact release identity, schema/integrity, parity, unchanged skills/MCP exposure, read-only UI/API, opposite-peer controlled restart, backup recovery and host hygiene.

## Offline commands

With the new release installed and the old writer stopped:

```sh
reach migrate-persistence --config /path/to/reach.yaml
reach export-persistence --config /path/to/reach.yaml --output /path/to/new-rollback-state
```

`workspace.database` selects the destination, or pass an explicit `--database`. Use `migrate-persistence --fresh` only when intentionally initializing empty state; it still preserves the configured Candidate authority boundary. Migration never starts the service. Export writes a new `runs/runs.sqlite3`, `tasks/active`, `tasks/archive` and configured `candidates` directory plus a verification receipt. Configure v0.10 against those roots only after its serving writer is stopped.

# Peer-local Run persistence

For the unified v0.11 path covering Runs, Tasks, leases and Candidates, see [Unified persistence](unified-persistence.md). This page describes the compatible v0.10 Run-only storage path used when `workspace.database` is omitted.

Runs use SQLite in `workspace.runs/runs.sqlite3`. Tasks and Candidates retain their existing storage. Run IDs, record schema versions, MCP/HTTP contracts, retention rules and executor ownership remain unchanged. Each peer owns its own local database; network filesystems, shared active-active databases and concurrent old/new writers are unsupported.

## Design decision

Repeated JSON parsing and directory enumeration made startup/reconciliation and filtered queries scale with every retained Run. Deferring reconciliation would postpone ambiguity handling; reducing retention would discard useful evidence; a separate JSON index would still require transactional repair and many filesystem objects. SQLite is in the Python standard library and provides atomic updates and indexed predicates with one local database. Status, task identity and cleanup eligibility are indexed. Payloads preserve historical schema fields without inventing new legacy meaning.

Short connections use SQLite's rollback journal and full synchronous durability; no WAL coordination or external database service is required. The existing peer-local write lock serializes read-modify-write operations. Read-only consumers cannot create a database or reconcile records. A consumer constructed before first migration switches to the database when it appears; once seen, disappearance fails closed instead of returning stale JSON.

## Upgrade

Stop the old peer before the first writable open. Preserve its release and the complete Runs directory as rollback evidence. First open creates a temporary database, validates every legacy JSON record and filename identity, imports all records in one transaction, records the count/content digest and timestamp in the `metadata` table, fsyncs, then atomically installs the database. Any validation/interruption failure leaves the old JSON untouched and no accepted partial database. A later open does not rescan JSON. Existing legacy JSON is retained as a pre-migration preimage, never a second writer or automatically re-imported source.

Normal startup reconciles only indexed running records for the caller's existing ownership modes: synchronous ServerRestart and asynchronous ExecutorRestart retain their established ambiguity semantics. Retention removes only eligible old, unambiguous, unretained terminal records. Interrupted/unknown and retained records remain protected. Migration is a one-time cost; measure subsequent startup independently.

## Rollback

Stop the new peer before rollback. Keep the new database and preimage. Using the new release, run:

```sh
reach export-runs-json --config /path/to/reach.yaml --output /path/to/new-rollback-runs
```

The output directory must not exist. Export includes post-migration Runs and current retained/status/result-reference state, writes mode-0600 JSON and an export receipt, and refuses overwrite. Validate the exported count and representative records, then configure the old release to use that new directory (or switch the peer-local directory while all writers are stopped). Do not merely delete SQLite or reuse stale pre-migration JSON: that would lose subsequent execution evidence. For a subsequent re-upgrade, migrate the accepted exported JSON directory afresh; never reuse the old database after old-version writes.

Do not remove preimages during rollout. Retirement requires deployment-owned acceptance and retention ownership. No tool output, commands, argument values or environment values are added to persisted Run payloads.

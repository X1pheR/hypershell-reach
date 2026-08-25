# Task storage migration

This document defines the public Hypershell Reach Task copy-and-validate contract. It does **not** authorize or perform a live deployment switch.

## Boundary

The migration helper is intentionally narrower than deployment orchestration:

1. inspect the source active and archive roots;
2. compute a deterministic preimage from every Task record and legacy evidence tree;
3. copy into empty target active and archive roots;
4. validate IDs, counts, record hashes, target placement and preserved evidence hashes;
5. re-inspect the source and reject source drift;
6. report whether the copy is ready for a separately governed switch.

The helper never changes Hypershell Reach configuration and never deletes, renames or retires the source roots.

## Public API

```python
from hypershell_reach.task_migration import (
    copy_and_validate_task_storage,
    inspect_task_storage,
)

preimage = inspect_task_storage(source_active, source_archive)
result = copy_and_validate_task_storage(
    source_active,
    source_archive,
    target_active,
    target_archive,
)
```

`inspect_task_storage` returns a deterministic manifest with source counts, Task IDs, source and intended target locations, exact `task.yaml` SHA-256 hashes, evidence state and a complete preimage SHA-256.

`copy_and_validate_task_storage` requires distinct source and target roots. Both target roots must be empty before copying and must reside on the same filesystem. It returns `switch_ready=true` only after the unchanged source and the copied target have both passed validation.

## Record preservation

The copy preserves each `task.yaml` byte-for-byte. This preserves Task IDs, schema version, revision when present, timestamps, status, retained state and continuity exactly. Existing Task v1 records therefore remain v1 during the copy; they are not eagerly rewritten to v2.

Terminal Task residue found in the source active root is copied directly into the target archive root without rewriting the YAML. This satisfies the target invariant that active storage contains only `active`, `partial` and `blocked` Task records while preserving the source record bytes and timestamps.

Run records are outside the Task migration write set. Existing `Run.task_id` values remain unchanged, and the migration never adds Run backlink fields to Task YAML.

## Legacy evidence directories

New Hypershell Reach Tasks no longer create `evidence/` directories. During migration:

- absent evidence directories remain absent;
- empty legacy evidence directories are omitted from the target copy and reported as removed-empty candidates;
- non-empty evidence directories are recursively copied, hashed and reported for operator review;
- evidence symlinks or unsupported filesystem entries fail the migration.

A non-empty evidence directory is never deleted by the public helper.

## Failure and rollback behavior

Malformed Task YAML, Task ID/directory mismatch, duplicate Task IDs across roots, non-terminal records already in the source archive, unexpected Task-directory entries, occupied target roots, source drift, count mismatch or hash mismatch fail the operation.

If copying or validation fails after the helper has started writing a previously empty target, the target copy is rolled back to its prior empty state. Pre-existing non-empty target data is rejected before any target mutation and is never removed. The source is not changed.

Before source retirement, rollback is therefore simply a deployment decision not to switch, or to switch configuration back while the untouched source roots remain available.

## Deployment gate

A live migration is a separate deployment operation. At minimum it should:

1. identify the exact public Hypershell Reach build and private deployment revision;
2. record current source counts and the migration preimage;
3. quiesce Task writers so the final copy cannot race with live mutations;
4. run copy-and-validate into the accepted target active/archive roots;
5. confirm `switch_ready=true`, counts, duplicate checks and any non-empty evidence review items;
6. change deployment-owned configuration from the legacy roots to the target roots;
7. start the compatible Hypershell Reach build only after the target binding is active;
8. verify Task reads, open-state invariants, archived terminal Tasks and existing `Run.task_id` relationships;
9. keep the legacy roots intact for rollback until the deployment acceptance window is complete;
10. retire legacy roots only through a separately authorized cleanup step.

Do not deploy a build with automatic Task repair against the legacy roots as an intermediate step: startup repair would legitimately close terminal residue before the governed copy/validate/switch migration has captured its final source preimage.

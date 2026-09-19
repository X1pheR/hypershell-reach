# Runs and tasks

Runs and tasks solve different problems.

## Runs

A run is one technical execution attempt. Synchronous `run_command`, `run_shell` and `run_script` create a Run immediately before the SSH execution boundary. Asynchronous `start_command`, `start_shell` and `start_script` create the Run in the durable executor when submission is accepted. New agent-initiated executions must provide a human-readable `purpose` that explains why the execution exists. Purpose is intentionally separate from the technical operation, target, managed-tool identity and execution result.

```mermaid
flowchart LR
    Call[Execution call] --> Mode{sync or async}
    Mode -->|sync| Start[Persist running Run]
    Mode -->|async| Accept[Executor accepts and persists running Run]
    Start --> SSH[Bounded SSH execution]
    Accept --> SSH
    SSH --> End[Persist terminal outcome]
    End --> Result[Read result through response or polling]
```

Run records contain bounded execution metadata only. They never persist:

- command text;
- shell or managed script content;
- argument values;
- environment values;
- stdout or stderr text;
- target addresses, users or credential paths.

A run can contain execution mode, purpose, script ID, source, content hash, argument names, target ID, timestamps, exit status, mutation classification, declared idempotency, output byte/truncation counters, a bounded `result_summary` and an optional caller-declared `result_ref`. Compact run listings expose purpose, result summary, result reference, `may_mutate` and `idempotent`. Managed tools persist their declared values; raw command and shell runs keep `idempotent: null` because Hypershell Reach cannot infer repeat safety from arbitrary caller input.

### Purpose contract

`purpose` answers **why** the execution exists. It must not repeat command text, shell/script bodies, argument values, environment values, credentials, tokens or other secrets. Agent-facing execution tools require it as one printable line of 1 to 512 characters after outer whitespace is trimmed. Invalid or oversized purpose is rejected rather than truncated.

Internal RunStore callers may omit purpose when no agent intent exists. Historical Run v1 records therefore remain meaningful with `purpose: null`; Hypershell Reach never invents purpose text for them.

### Result summary contract

`result_summary` is server-generated result/diagnostic context, not caller-supplied log storage. It is built only from allowlisted metadata already inside the Run safety boundary: terminal status, exit code, configured timeout, ambiguity classification, stdout/stderr byte counts and truncation flags, or a bounded internal error type. It never includes command text, script content, argument values, environment values, or stdout/stderr content.

A persisted result summary is one printable line with a hard maximum of 512 characters. Hypershell Reach deterministically truncates an internally generated summary that would exceed the limit and appends ` [truncated]`; persisted values beyond the schema bound are rejected. This does not create a second receipt, log or artifact repository: the Run remains the execution receipt.

### Execution class contract

Agent execution calls accept `execution_class: normal|heavy`, defaulting to `normal`. The class is explicit caller intent; Reach never guesses workload weight from command text, script content, model name or target capability. Runs persist the class for observability.

A `heavy` class has no effect unless the selected target configures `max_heavy_concurrency`. When a limit is configured, synchronous and asynchronous owners coordinate through fixed advisory lease files in the Run root. If all heavy slots are occupied, a new heavy execution is rejected before remote SSH starts. Normal executions do not consume a heavy slot, and targets without a configured limit retain their existing concurrency behavior.

### Result reference contract

`result_ref` is an optional caller-declared pointer to bounded decision evidence that the execution is expected to publish, such as a report path or stable application/state identifier. Reach stores only the pointer and never dereferences, copies or validates the referenced content. It is intended to make durable or transport-risk Runs reconstructable when stdout is not available after the initiating client turn.

The value is trimmed, must be one printable non-empty line and is limited to 512 characters. It must not contain credentials, tokens, secret values, command text or other sensitive payloads. A persisted `result_ref` does not prove that the referenced artifact exists or is valid; terminal Run status plus the referenced owner's own postconditions remain authoritative.

### Schema compatibility

New Run writes use schema v4. The reader accepts Run v1 through v4, so existing stores require no bulk migration for forward operation. Run v1 has no purpose or result summary; Run v1 and v2 have no persisted `execution_mode` and are projected as `sync`; Run v1 through v3 have no `result_ref`. Historical records are not implicitly rewritten. Schema v3 made executor ownership durable state; schema v4 adds only the bounded result pointer without persisting result content.

### States

| State | Meaning |
| --- | --- |
| `running` | Local execution attempt is in progress. |
| `succeeded` | Remote exit code was zero. |
| `remote_error` | Remote process returned a known non-zero exit code. |
| `transport_error` | SSH transport failed. |
| `timeout` | Hypershell Reach killed the local SSH process after its timeout. |
| `local_error` | Execution could not start or complete because of a local Hypershell Reach/runtime error. |
| `interrupted` | Hypershell Reach execution was cancelled or a prior `running` record was recovered after restart. |
| `unknown` | An unexpected internal failure prevented a trustworthy terminal classification. |

For potentially mutating operations, `transport_error`, `timeout`, `interrupted` and `unknown` are marked `ambiguous=true`. Hypershell Reach does not retry them automatically.

Raw `run_command` and `run_shell` are treated as potentially mutating because Hypershell Reach cannot infer their semantics. Managed tools use their declared `mutating` metadata.

### Recovery

Recovery is ownership-specific. The MCP runtime reconciles only stale synchronous `running` records as `interrupted` with `ServerRestart`; the separately supervised executor reconciles only stale asynchronous `running` records with `ExecutorRestart`. In addition, each live owner closes its own in-process ownership gap: if a synchronous execution scope exits while its Run is still `running`, Reach marks that Run `unknown` with `ServerOwnershipLost`; if an asynchronous executor task completes while its Run is still `running`, Reach marks it `unknown` with `ExecutorOwnershipLost`. These guards are based on demonstrable local ownership loss, not Run age, so a healthy long-running remote execution is never classified stale merely because it is slow. Starting or reconnecting one runtime cannot interrupt work owned by the other. These states record local execution uncertainty only; they do not claim the remote system rolled back or completed.

### Run tools

- `list_runs` returns bounded summaries and can filter by state or task ID.
- `get_run` returns one complete metadata record.
- `set_run_retained` sets a local retention override without executing anything remotely.
- `cancel_run` explicitly cancels one running async Run through the executor and requires confirmation.

Run mutations from the MCP and executor processes are serialized through one fixed advisory write lock in the Run root. Writes remain atomic replacements; the fixed lock prevents cross-process lost updates without introducing a database or an unbounded lockfile set.

## Tasks

A Task is durable continuity state for work that would be difficult to reconstruct after interruption or handoff. Ordinary commands and straightforward reproducible changes do not need a Task. A Task is not authorization and is not a project-management record.

```mermaid
flowchart LR
    Task[Task] --> R1[Run]
    Task --> R2[Run]
    Task --> R3[Run]
```

Runs persist `task_id`; Tasks do not persist a backlink list. Reverse Task-to-Run views are therefore derived from Run records.

### Storage and schema

The active and archive roots are configured independently through the backward-compatible `workspace.tasks` and `workspace.trash` keys. A deployment may bind them to paths such as:

```text
appdata/reach/tasks/
├── active/
│   ├── .locks/
│   └── <task-id>/task.yaml
└── archive/
    └── <task-id>/task.yaml
```

`.locks/` is store metadata, not Task state. New Tasks do not create `evidence/` directories. Task YAML never persists physical storage paths, secrets, or Run backlinks.

Task v2 adds a monotonic `revision`; new records start at revision `1`. Existing Task v1 records remain readable without being rewritten and are projected as revision `0` in memory. The first successful mutation of a v1 record writes Task v2 and advances its revision. Unknown persisted fields fail validation.

The `continuity` snapshot remains bounded. On `update_task` and `close_task`, continuity is merge-patched: omitted fields are preserved, explicitly supplied fields replace that field, and an explicit empty list clears a list field. It can hold authorization context, authoritative sources, completed material work, validation, cleanup, recovery, blockers and material assumptions. It is intended for safe resume or handoff, not command history.

### Mutation concurrency and durability

Task creation uses one store-wide interprocess creation lock so equivalent concurrent creators cannot commit duplicate open continuity records. Open-Task equivalence is the case-sensitive `(title, objective, project_ref)` tuple after trimming leading and trailing whitespace from each present value. If an equivalent `active`, `partial` or `blocked` Task already exists, `create_task` returns that record instead of creating a second Task. The repeated create does not merge or overwrite `next_action`, `continuity` or `retained`; callers that intend to change existing continuity state must use `update_task`. Archived or otherwise terminal Tasks do not participate in this create-time equivalence check, so a later genuinely new continuity unit remains creatable.

Task mutations use a narrowly scoped per-Task interprocess lock. `expected_revision` provides compare-and-swap semantics for callers that perform read-modify-write operations: a stale revision is rejected and cannot overwrite a newer committed Task state. The field remains optional on `update_task` and `close_task` for compatibility with the pre-v2 MCP input contract; compatibility calls are still serialized and apply typed partial mutations rather than arbitrary document replacement. Nested continuity updates use the same patch semantics, so a validation-only update cannot erase previously committed cleanup, recovery or source state.

Every Task YAML mutation writes a complete validated record to a temporary file inside the same Task directory, fsyncs the file, atomically replaces `task.yaml`, and fsyncs the containing directory. Creation additionally fsyncs the active root. A close writes and fsyncs the final terminal record before the directory move, then atomically moves the Task directory on the same filesystem and fsyncs both active and archive roots. Malformed Task-shaped filesystem entries and duplicate Task IDs across roots fail safe.

### States and closure

Task states are `active`, `partial`, `blocked`, `completed` and `cancelled`. Only `active`, `partial` and `blocked` may remain in the active Task root after normal operation or startup recovery. Only open Tasks accept new linked Runs.

A Task-linked Reach execution that is potentially mutating creates one reserved `[reach:pending-mutation]` blocker before remote dispatch or durable asynchronous submission. Raw command/shell executions are treated as potentially mutating; managed scripts use their registry-owned `mutating` metadata. Repeated mutations keep one generated blocker representing all task-linked mutations since the last reconciliation, with the latest execution purpose as bounded context. The blocker can be released only through `update_task(reconcile_mutation=...)`, which records the supplied postcondition evidence in `continuity.validation`.

`close_task` is the explicit terminal boundary. It owns the status transition, final metadata update, durable Task record and active-to-archive move. A new `completed` close fails while `next_action` is still present or any continuity blocker remains. This makes postcondition reconciliation a technical completion gate rather than a caller convention. `cancelled` may still archive intentionally abandoned work with unresolved state. For backward compatibility, `update_task` with `status=completed|cancelled` enters the same close boundary, and `archive_task` remains an idempotent compatibility helper for already-terminal callers.

A retry of the same close request returns the committed archived record without incrementing the revision again. If the final terminal YAML committed but the directory move was interrupted, retry completes the move. If the rename committed but root-directory fsync failed, retry re-establishes the required fsync boundary. Conflicting terminal outcomes fail rather than silently rewriting final state.

On writable server startup Hypershell Reach runs Task repair before retention. Terminal residue in the active root is completed into the archive root; `active`, `partial` and `blocked` records are not moved. Duplicate IDs or malformed records stop repair rather than guessing.

### Task tools

- `list_tasks` returns bounded current Tasks and can optionally include archived Tasks.
- `get_task` returns one current or archived Task.
- `create_task` creates Task v2 continuity state without executing remotely, or returns an equivalent open Task when the normalized continuity identity already exists.
- `update_task` applies a typed merge-safe partial update and supports `expected_revision` CAS. `reconcile_mutation` records postcondition evidence and clears Reach's generated pending-mutation blocker. A terminal status uses the close boundary and cannot simultaneously perform reconciliation.
- `close_task` atomically closes and archives a Task from the caller perspective; completed closure requires no remaining next action or blockers.
- `archive_task` is retained for backward compatibility with the former two-step lifecycle.

## Retention

Retention is configuration-driven and disabled by default.

A completed Run can be deleted automatically only when all conditions are true:

- it has a cleanup-eligible terminal state;
- it is older than `retention.runs.completed_days`;
- `ambiguous=false`;
- `retained=false`.

`running`, `interrupted`, `unknown` and ambiguous Run records are never removed by Run cleanup.

Task retention considers only records physically present in the archive root. A Task can be deleted only when it is terminal, has a valid `archived_at`, has `retained=false`, and is older than `retention.tasks.archived_days`. `active`, `partial` and `blocked` Tasks are never deleted by Task retention, even if they are old or misplaced.

## Task storage migration

The public migration helper implements the copy-and-validate portion of a governed deployment migration. It computes a deterministic source preimage, validates record counts and duplicate IDs, byte-copies Task YAML, routes terminal active residue into the target archive, omits only empty legacy `evidence/` directories, preserves and reports every non-empty legacy `evidence/` tree, revalidates the unchanged source, and validates the target before reporting `switch_ready=true`.

It never edits deployment configuration, retires the source roots, or performs a live switch. The deployment gate must quiesce writers, establish the exact preimage, run copy/validate, change the configured roots, start the compatible Hypershell Reach build against the target roots, validate Task and Run relationships, and keep the legacy source roots available until rollback is no longer required. See [Task storage migration](task-storage-migration.md).

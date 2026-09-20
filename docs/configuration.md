# Configuration

Hypershell Reach reads YAML from the path in `REACH_CONFIG`. Configuration is deployment-owned and must not contain private-key contents.

## Root

```yaml
schema_version: 1
topology: {}  # optional for standalone deployments
workspace: {}
defaults: {}
executor: {}
sources: {}
targets: {}
```

Unknown fields fail validation.

## Topology

```yaml
topology:
  node_id: home
  primary_node_id: home
```

`topology` is an optional declarative deployment identity. `node_id` identifies the local Reach node and `primary_node_id` names the configured normal primary. Both use the same stable lowercase ID syntax as target IDs. Reach derives the local role as `primary` when both IDs match and `standby` otherwise, and exposes that safe metadata through `/api/v1/summary` when topology is configured.

This contract does not add peer discovery, leader election, automatic failover, state replication or distributed coordination. Routing and recovery remain deployment/consumer responsibilities, and omitting `topology` preserves standalone deployments.

## Workspace

```yaml
workspace:
  tmp: /var/tmp/reach
  runs: /var/lib/reach/runs
  tasks: /var/lib/reach/tasks
  trash: /var/lib/reach/trash
  candidates: /var/lib/reach/candidates  # optional until the deployment migration gate
```

All configured workspace paths are explicit absolute paths. `runs` stores automatic execution metadata. `tasks` is the active Task root and `trash` is the backward-compatible configuration key for the Task archive root. New Task records use the Task v2 contract; existing Task v1 YAML remains readable. `candidates`, when configured, stores one Hypershell Reach-owned `candidate-v1` YAML record per Candidate. Candidate v1 now carries monotonic `problem.recurrence_count` with default `1`; existing v1 records that contain only the legacy free-text `problem.recurrence` remain readable and resolve to count `1` until a later distinct occurrence is recorded. Omitting `candidates` preserves deployments that have not reached the Candidate storage migration gate. Managed tool sources stay separate and must not be placed under Candidate appdata.

## Retention

```yaml
retention:
  runs:
    completed_days: null
  tasks:
    archived_days: null
```

`null` disables automatic cleanup. Run cleanup never removes ambiguous, interrupted, unknown or explicitly retained records. Task cleanup considers only terminal records already present in the Task archive root with a valid `archived_at`, `retained=false`, and an age beyond `retention.tasks.archived_days`. Active, partial and blocked Tasks are never retention candidates.

## Execution defaults

```yaml
defaults:
  connect_timeout_seconds: 10
  max_timeout_seconds: 300
  max_synchronous_timeout_seconds: 90
  max_output_bytes: 262144
```

`max_timeout_seconds` is the execution capability limit. `max_synchronous_timeout_seconds` is a separate transport-delivery contract: synchronous `run_*` calls above that value are rejected before SSH and must use the corresponding `start_*` operation. This prevents a deployment from advertising synchronous execution durations that its primary MCP transport cannot reliably return. Omitting the synchronous limit preserves the historical behavior by resolving it to `max_timeout_seconds`.

Targets may override these limits within the schema bounds. An effective synchronous limit may not exceed the effective execution limit.

## Execution manager

```yaml
executor:
  max_concurrency: 2
```

The execution manager is integrated into the long-lived Reach service. `start_command`, `start_shell` and `start_script` create an asynchronous Run and return its ID quickly. Accepted work is independent of the individual MCP request lifetime but remains owned by the Reach process lifecycle.

`max_concurrency` bounds simultaneous asynchronous SSH executions. Per-target `max_heavy_concurrency` is independent: it is an opt-in cross-process guard shared by synchronous and asynchronous executions classified as `heavy`. Reach does not infer workload weight from command text. Reach does not require an external queue, worker process or Unix socket in the maintained deployment model.

## Managed tool sources

```yaml
sources:
  tools:
    - id: reach
      type: bundled
      enabled: true
    - id: local
      type: filesystem
      path: /sources/local-tools
      enabled: true
```

`bundled` selects managed tools shipped inside the installed Hypershell Reach package and therefore has no configured path. `filesystem` reads an explicit absolute deployment-owned directory. Disabled sources remain configured but are not scanned.

Filesystem source delivery through Git checkout, bind mounts, SMB, rsync or another mechanism remains a deployment responsibility. Bundled tools are versioned with the Hypershell Reach package, so package installation does not require a second checkout for the standard tool set.

Source IDs must be unique. Managed script IDs must be globally unique across enabled sources. A duplicate is a configuration/runtime error rather than a precedence rule.

## Tooling registry

```yaml
sources:
  tooling_registry:
    type: markdown
    path: /sources/tooling-registry.md
    enabled: true
```

The tooling registry is an optional deployment-owned **legacy compatibility feed**. `tooling_candidates` retains its existing read-only result for current consumers, and `preview_candidate_imports` maps only explicitly declared legacy candidates to incomplete `candidate-v1` drafts. Hypershell Reach does not infer missing problem, safety, ownership, interface or acceptance facts from prose.

A legacy candidate entry must declare `Status: observed|guarded`, `Promotion: candidate`, a non-empty `Promotion reason` and a non-empty `Helper candidate or implementation` field. Entries without a `Promotion` field remain valid and are omitted from the legacy candidate view. The Markdown source is never mutated by the Candidate lifecycle API.

New mutable Candidate state is enabled independently through `workspace.candidates`. This additive split allows repository contracts and compatibility tests to land before a deployment performs a governed copy/validate/switch migration.

## Targets

```yaml
targets:
  example:
    display_name: Example host
    transport: ssh
    capabilities: [linux, bash, docker]
    enabled: true
    max_heavy_concurrency: 1
    ssh:
      host: 192.0.2.10
      port: 22
      user: operator
      identity_file: /run/secrets/reach/id_ed25519
      known_hosts_file: /run/secrets/reach/known_hosts
```

Target IDs use lowercase letters, numbers and hyphens. `list_targets` returns IDs, display names, capabilities, the effective execution timeout, the effective synchronous transport-safe timeout and `max_heavy_concurrency`. It never returns host addresses, usernames or credential paths.

`capabilities` are compatibility tags. They are not an authorization system.

`max_heavy_concurrency` is optional and unset by default. When configured, only executions explicitly submitted with `execution_class: heavy` consume one of that target's heavy slots. `execution_class: normal` is unaffected. Targets that omit the setting remain unrestricted by this per-target heavy guard; the executor's global `max_concurrency` still applies to asynchronous work.

## Skill sources

```yaml
sources:
  skills:
    - id: local
      type: filesystem
      path: /sources/local-skills
      os_platform: linux

    - id: hermes
      type: hermes
      path: /sources/hermes-skills
      os_platform: linux
      state:
        mode: remote
        target: hermes
        python_executable: /opt/hermes-agent/venv/bin/python
        config_path: /home/operator/.hermes/config.yaml
        repo_path: /opt/hermes-agent
        consumer_platform: cli
```

`mode: remote` is the default and uses the live bounded Hermes projection. Use the Python interpreter from the deployed Hermes runtime environment rather than an unrelated system interpreter. The projector imports Hermes' current skill loader, so matching the runtime interpreter avoids false missing-dependency or plugin-import failures.

A standby that must serve the last accepted semantic skill state without contacting Hermes can use snapshot mode instead:

```yaml
sources:
  skills:
    - id: hermes
      type: hermes
      path: /sources/hermes-snapshot/current/skills
      additional_paths:
        - /sources/hermes-snapshot/current/workspace-skills
      os_platform: linux
      state:
        mode: snapshot
        snapshot_path: /sources/hermes-snapshot/current/state.json
```

Snapshot mode is mutually exclusive with the remote projection fields. The state file is content-bound and revision-bound; Hypershell Reach recomputes the local semantic content fingerprint before accepting it.

Skill IDs are source-qualified, so the same bare skill name can exist in different sources without shadowing. Within one source a duplicate bare skill name is rejected.

A Hermes source requires either a bounded remote state projection or a local verified snapshot state. Remote mode executes only the Hypershell Reach-owned read-only projector over the configured target and returns a sanitized effective catalog. Snapshot mode performs no Hermes network call and accepts state only when source ID, schema, canonical revision and local semantic content fingerprint match. See [Skills](skills.md).

## Local validation

Run the operator preflight before registering Hypershell Reach with an MCP client:

```bash
REACH_CONFIG=/path/to/reach.yaml reach validate
```

Or select the file explicitly:

```bash
reach validate --config /path/to/reach.yaml
```

The command validates the typed configuration and local runtime prerequisites. It reports:

- workspace paths and whether they are writable or creatable from a writable parent;
- enabled target IDs, display names, SSH hosts, users and capabilities;
- enabled managed-tool source paths and discovered script counts;
- enabled skill source paths and discovered local package counts;
- Hermes state-target names and hosts, without contacting Hermes;
- the fixed SSH executable and configured identity/known-host paths as present/readable checks.

`validate` does not make network connections and never prints credential contents. Workspace writability checks use short-lived local probe files that are removed immediately. For Hermes sources the local content tree is validated, but effective enable/disable state is reported as not checked because that requires the normal runtime state projection.

Exit codes are stable:

- `0` — configuration and local prerequisites are valid;
- `1` — configuration or a local runtime prerequisite is invalid;
- `2` — CLI usage error.

The normal MCP surface remains intentionally less revealing: `list_targets` does not expose hosts, users or credential paths.

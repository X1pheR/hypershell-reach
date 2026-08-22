# Configuration

HATS reads YAML from the path in `HATS_CONFIG`. Configuration is deployment-owned and must not contain private-key contents.

## Root

```yaml
schema_version: 1
workspace: {}
defaults: {}
sources: {}
targets: {}
```

Unknown fields fail validation.

## Workspace

```yaml
workspace:
  tmp: /var/tmp/hats
  runs: /var/lib/hats/runs
  tasks: /var/lib/hats/tasks
  trash: /var/lib/hats/trash
  candidates: /var/lib/hats/candidates  # optional until the deployment migration gate
```

All configured workspace paths are explicit absolute paths. `runs` stores automatic execution metadata; `tasks` and `trash` are reserved for task continuity. `candidates`, when configured, stores one HATS-owned `candidate-v1` YAML record per Candidate. Omitting `candidates` preserves deployments that have not reached the Candidate storage migration gate. Managed tool sources stay separate and must not be placed under Candidate appdata.

## Retention

```yaml
retention:
  runs:
    completed_days: null
  tasks:
    archived_days: null
```

`null` disables automatic cleanup. Run cleanup never removes ambiguous, interrupted, unknown or explicitly retained records. Task cleanup is added with the task lifecycle.

## Execution defaults

```yaml
defaults:
  connect_timeout_seconds: 10
  max_timeout_seconds: 300
  max_output_bytes: 262144
```

Targets may override these limits within the schema bounds.

## Managed tool sources

```yaml
sources:
  tools:
    - id: hats
      type: bundled
      enabled: true
    - id: local
      type: filesystem
      path: /sources/local-tools
      enabled: true
```

`bundled` selects managed tools shipped inside the installed HATS package and therefore has no configured path. `filesystem` reads an explicit absolute deployment-owned directory. Disabled sources remain configured but are not scanned.

Filesystem source delivery through Git checkout, bind mounts, SMB, rsync or another mechanism remains a deployment responsibility. Bundled tools are versioned with the HATS package, so package installation does not require a second checkout for the standard tool set.

Source IDs must be unique. Managed script IDs must be globally unique across enabled sources. A duplicate is a configuration/runtime error rather than a precedence rule.

## Tooling registry

```yaml
sources:
  tooling_registry:
    type: markdown
    path: /sources/tooling-registry.md
    enabled: true
```

The tooling registry is an optional deployment-owned **legacy compatibility feed**. `tooling_candidates` retains its existing read-only result for current consumers, and `preview_candidate_imports` maps only explicitly declared legacy candidates to incomplete `candidate-v1` drafts. HATS does not infer missing problem, safety, ownership, interface or acceptance facts from prose.

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
    ssh:
      host: 192.0.2.10
      port: 22
      user: operator
      identity_file: /run/secrets/hats/id_ed25519
      known_hosts_file: /run/secrets/hats/known_hosts
```

Target IDs use lowercase letters, numbers and hyphens. `list_targets` returns IDs, display names, capabilities and effective limits. It never returns host addresses, usernames or credential paths.

`capabilities` are compatibility tags. They are not an authorization system.

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
        target: hermes
        python_executable: /opt/hermes-agent/venv/bin/python
        config_path: /home/operator/.hermes/config.yaml
        repo_path: /opt/hermes-agent
        consumer_platform: cli
```

Use the Python interpreter from the deployed Hermes runtime environment rather than an unrelated system interpreter. The projector imports Hermes' current skill loader, so matching the runtime interpreter avoids false missing-dependency or plugin-import failures.

Skill IDs are source-qualified, so the same bare skill name can exist in different sources without shadowing. Within one source a duplicate bare skill name is rejected.

A Hermes source requires a bounded state projection target. The content path is read locally; the projection executes only the HATS-owned read-only projector over the configured target and returns a sanitized effective catalog. See [Skills](skills.md).

## Local validation

Run the operator preflight before registering HATS with an MCP client:

```bash
HATS_CONFIG=/path/to/hats.yaml hats-mcp validate
```

Or select the file explicitly:

```bash
hats-mcp validate --config /path/to/hats.yaml
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

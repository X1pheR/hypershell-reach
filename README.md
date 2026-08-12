# HATS

Homelab Agent Tooling & Skills MCP provides reusable agent tooling, bounded remote execution and shared skill discovery for homelabs.

> Pre-release project. Interfaces may change before the first stable release.

## What it is

HATS is a small MCP service for capabilities that are specific to a homelab and are not already provided by an upstream MCP server.

```mermaid
flowchart LR
    Agent[Agent] --> Gateway[MCP gateway]
    Gateway --> HATS[HATS]
    HATS --> Targets[Configured targets]
    HATS --> Tools[Managed tool sources]
    HATS --> Skills[Skill sources]
```

The current implementation provides configured targets, bounded SSH execution, registry-backed managed scripts, persistent run metadata, task continuity and progressive read-only Agent Skills discovery.

## Design

HATS is a modular monolith. Deployment-specific hosts, paths and source locations stay in configuration. The repository contains no deployment-specific configuration or private homelab data.

See [Architecture](docs/architecture.md) for module boundaries and delivery phases.

## Configuration

HATS reads one YAML configuration file selected by `HATS_CONFIG`.

```yaml
schema_version: 1
workspace:
  tmp: /var/tmp/hats
  runs: /var/lib/hats/runs
  tasks: /var/lib/hats/tasks
  trash: /var/lib/hats/trash

sources:
  tools:
    - id: hats
      type: bundled
      enabled: true
    - id: local
      type: filesystem
      path: /sources/local-tools
      enabled: true

targets:
  example:
    display_name: Example host
    transport: ssh
    capabilities: [linux, bash]
    ssh:
      host: 192.0.2.10
      user: operator
      identity_file: /run/secrets/hats/id_ed25519
      known_hosts_file: /run/secrets/hats/known_hosts
```

The `bundled` source enables standard managed tools shipped with the installed HATS package. Additional deployment-owned tools can be added through filesystem sources.

See [Configuration](docs/configuration.md) and [`examples/config.example.yaml`](examples/config.example.yaml).

Before connecting HATS to an MCP client, validate the local deployment:

```bash
HATS_CONFIG=/path/to/hats.yaml hats-mcp validate
```

`validate` is an operator-facing preflight. It shows configured target names and hosts, workspace readiness, source paths and SSH credential paths without printing credential contents or making network connections.

## MCP surface

HATS currently exposes:

- `list_targets`
- `list_scripts`
- `get_script`
- `run_script`
- `list_runs`
- `get_run`
- `set_run_retained`
- `list_tasks`
- `get_task`
- `create_task`
- `update_task`
- `archive_task`
- `skills_catalog` — compact live tier-1 discovery
- `skill_get` — bounded selected skill content
- `skill_read_file` — bounded supporting-file retrieval
- `run_command`
- `run_shell`

Skill content remains read-only and never becomes executable tooling automatically.

See [Tool contracts](docs/tools.md) and [Skills](docs/skills.md).

## Security

HATS does not authorize an agent to perform a change. It provides bounded execution and managed capability discovery. The caller remains responsible for deciding whether an operation is authorized.

See [Security](docs/security.md).

## Development

See [Development](docs/development.md).

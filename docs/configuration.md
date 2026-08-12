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
  tasks: /var/lib/hats/tasks
  trash: /var/lib/hats/trash
```

All workspace paths are explicit absolute paths. They are reserved for later run/task state and do not contain managed tool sources.

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
    - id: bundled
      type: filesystem
      path: /app/tools
    - id: local
      type: filesystem
      path: /sources/local-tools
```

Source IDs must be unique. Paths are absolute and deployment-owned. Disabled sources remain configured but are not scanned.

HATS reads the configured directories directly. Git checkout, bind mounts, SMB, rsync or other source-delivery mechanisms remain deployment responsibilities.

Managed script IDs must be globally unique across enabled sources. A duplicate is a configuration/runtime error rather than a precedence rule.

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

Skill source configuration is added in the skills phase. It uses the same deployment-owned source principle but has different duplicate and compatibility semantics from executable tools.

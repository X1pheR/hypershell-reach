# Configuration

HATS reads YAML from the path in `HATS_CONFIG`. Configuration is deployment-owned and must not contain private-key contents.

## Root

```yaml
schema_version: 1
workspace: {}
defaults: {}
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

All workspace paths are explicit absolute paths. Phase 1 validates them but does not create task state.

## Execution defaults

```yaml
defaults:
  connect_timeout_seconds: 10
  max_timeout_seconds: 300
  max_output_bytes: 262144
```

Targets may override these limits within the schema bounds.

## Targets

```yaml
targets:
  docker:
    display_name: Docker host
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

`capabilities` are descriptive compatibility tags. They are not an authorization system.

## Source configuration

Managed tool and skill source configuration is reserved for later phases. The design supports multiple ordered sources without requiring HATS to manage Git or filesystem synchronization itself.

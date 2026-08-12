# Tool contracts

HATS keeps the MCP surface small. Tools return bounded structured JSON as text in phase 1.

## `list_targets`

Returns configured target IDs and safe metadata.

The result excludes host addresses, usernames, identity paths and known-hosts paths.

## `run_command`

Runs one non-interactive command on one configured target.

Inputs:

- `target`
- `command`
- `timeout_seconds`

The requested timeout cannot exceed the configured target maximum. Commands are never retried automatically.

## `run_shell`

Runs a `sh` or `bash` script over SSH stdin. Use it for loops, here-documents and multi-step shell input where nested command quoting would be fragile.

Inputs:

- `target`
- `interpreter`
- `script`
- `timeout_seconds`

## Managed tools

Phase 2 adds a registry-backed surface:

```text
list_scripts()
get_script(script_id)
run_script(script_id, target, arguments)
```

`run_script` will accept only registered script IDs. It will not accept arbitrary filesystem paths or unrestricted environment injection.

Managed tool metadata can declare requirements such as `docker` or `bash`. Target capabilities provide a simple pre-execution compatibility check.

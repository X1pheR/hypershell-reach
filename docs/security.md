# Security

HATS exposes powerful execution capabilities. Keep the service private and expose only the MCP tools required by each client.

## Execution

SSH execution enforces:

- key-only non-interactive authentication;
- strict host-key checking;
- no user SSH configuration;
- no PTY;
- no agent or port forwarding;
- no proxy or local command;
- one connection attempt;
- bounded timeout and output;
- no automatic retry;
- redaction of configured host and credential paths from returned SSH errors.

Configuration selects the host, user, identity and known-hosts file. The caller cannot replace those values per request.

## Authorization

HATS execution controls do not authorize a requested change. A caller or agent must still apply its own operating rules before disruptive, destructive or security-sensitive operations.

Managed tool metadata is descriptive execution metadata, not a replacement authorization policy.

## Skills

Skill sources are read-only. Retrieval must reject path traversal and reads outside approved source roots.

Skill content can contain unsafe or irrelevant instructions. Loading a skill does not authorize executing those instructions.

## Secrets

Do not put private keys or secret values in HATS configuration, tool metadata, logs, run records or task records. Secret files are referenced by path and mounted separately by the deployment.

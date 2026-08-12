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

## Managed tools

`run_script` resolves only configured source roots and exact registered IDs. It does not accept filesystem paths, raw argv or arbitrary environment variables.

Tool sources are trusted executable sources. Keep them under normal source-control and filesystem access controls. Discovery rejects symlinked managed files and symlinked subdirectories so a configured root cannot silently redirect execution to unrelated filesystem content.

Typed arguments are validated before execution and converted to deterministic option/value pairs. This bounds the caller interface but does not make a mutating script safe or authorized.

## Authorization

HATS execution controls do not authorize a requested change. A caller or agent must still apply its own operating rules before disruptive, destructive or security-sensitive operations.

Managed tool metadata is descriptive execution metadata, not an authorization policy. `mutating` and `idempotent` communicate behavior; they do not grant permission.

## Skills

Skill sources are read-only. Retrieval must reject path traversal and reads outside approved source roots.

Skill content can contain unsafe or irrelevant instructions. Loading a skill does not authorize executing those instructions. Scripts inside skill packages do not become managed tools automatically.

## Secrets

Do not put private keys or secret values in HATS configuration, tool metadata, logs, run records or task records. Secret files are referenced by path and mounted separately by the deployment.

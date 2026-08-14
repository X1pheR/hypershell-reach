# Installation

HATS releases publish a Python wheel, source distribution and SHA-256 manifest through GitHub Releases. HATS is not published to a language package registry.

## Requirements

- Python 3.12 or newer;
- `uv` for source development or environment management;
- OpenSSH client when SSH targets are configured;
- deployment-owned SSH identities and known-host files;
- one HATS YAML configuration file.

## Install an immutable release

Download the wheel and `SHA256SUMS` from the selected GitHub release, verify the wheel against the manifest, and install that exact artifact into the deployment environment.

Example after the release assets have been obtained locally:

```bash
sha256sum --check SHA256SUMS --ignore-missing
uv tool install ./homelab_agent_tooling_skills_mcp-X.Y.Z-py3-none-any.whl
```

The base install provides the STDIO command:

```bash
hats-mcp
```

The same wheel also installs the optional Web UI runtime:

```bash
uv tool install ./homelab_agent_tooling_skills_mcp-X.Y.Z-py3-none-any.whl
hats-ui --host 127.0.0.1 --port 8080
```

Validate configuration and local prerequisites before connecting an MCP client:

```bash
HATS_CONFIG=/path/to/hats.yaml hats-mcp validate
```

## Development from a reviewed checkout

A development environment may run directly from an exact clean checkout:

```bash
uv run --frozen --directory /path/to/hats-source hats-mcp
```

This is a development and transition model. Maintained deployment should prefer a verified release artifact so runtime does not depend on mutable Git checkout state.

## Upgrade

1. Review the new release notes and checksum manifest.
2. Install the selected exact wheel into the deployment environment.
3. Keep deployment configuration separate from the generic product source.
4. Run `hats-mcp validate`.
5. Start or refresh only the HATS MCP child.
6. Re-run consumer acceptance for target execution, managed tools, tooling candidates and configured skill adapters that changed.

Persistent Runs and Tasks live in configured workspace paths and are independent of the Python package installation. Do not remove those paths as part of a normal package upgrade.

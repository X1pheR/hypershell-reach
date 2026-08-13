# Installation

HATS currently ships as source and is not published to a package registry.

## Requirements

- Python 3.12 or newer;
- `uv`;
- OpenSSH client when SSH targets are configured;
- deployment-owned SSH identities and known-host files;
- one HATS YAML configuration file.

## Install from a reviewed checkout

Clone or otherwise obtain the source at a revision you intend to run, then install it locally:

```bash
uv tool install .
```

The installed command is:

```bash
hats-mcp
```

Validate configuration and local prerequisites before connecting an MCP client:

```bash
HATS_CONFIG=/path/to/hats.yaml hats-mcp validate
```

## Run from a pinned checkout

A deployment can also keep HATS source-managed and run directly from an exact reviewed checkout:

```bash
uv run --frozen --directory /path/to/hats-source hats-mcp
```

For this model, keep the checkout clean and verify the expected Git revision before starting the MCP child. HATS does not require Git at runtime itself; the revision check is a deployment concern.

## Upgrade

1. Review and test the new source revision.
2. Update the local checkout or installed tool.
3. Keep the deployment configuration separate from the generic source tree.
4. Run `hats-mcp validate`.
5. Start or refresh only the HATS MCP child.
6. Re-run consumer acceptance for target execution, managed tools and any configured skill adapters that changed.

Persistent Runs and Tasks live in configured workspace paths and are independent of the Python package installation. Do not remove those paths as part of a normal package upgrade.

## Package registry

No public package-registry release is claimed by the current project. If a registry distribution is added later, document the exact package name and release verification here rather than relying on an unverified installation command.

# Installation

Hypershell Reach releases publish a Python wheel, source distribution and SHA-256 manifest through GitHub Releases.

## Requirements

- Python 3.12 or newer;
- OpenSSH client for configured SSH targets;
- deployment-owned SSH identities and known-host files;
- one Reach YAML configuration file.

## Install a release

Verify the selected release assets and install the exact wheel:

```bash
sha256sum --check SHA256SUMS --ignore-missing
uv tool install ./hypershell_reach-X.Y.Z-py3-none-any.whl
```

The installed product has one public command:

```bash
reach
```

Validate deployment prerequisites:

```bash
REACH_CONFIG=/path/to/reach.yaml reach validate
```

Start the complete service:

```bash
REACH_CONFIG=/path/to/reach.yaml reach --host 0.0.0.0 --port 8080
```

The service provides MCP, the read-only API, Web UI and asynchronous execution manager on one lifecycle.

## Container deployment

The repository Dockerfile builds the same product and exposes port `8080`. Mount the Reach configuration, persistent workspace, configured tool/skill sources and SSH credentials according to deployment policy. Do not bake deployment secrets into the image.

## Development

A reviewed checkout can run with the frozen lockfile:

```bash
uv sync --frozen --extra dev
REACH_CONFIG=/path/to/reach.yaml uv run --frozen reach
```

Maintained deployment should use a verified release artifact rather than a mutable source checkout.

## Upgrade

1. Review release notes and checksums.
2. Back up or snapshot deployment configuration and persistent Reach state according to deployment policy.
3. Install or pull the exact new Reach release.
4. Run `reach validate` against the deployment configuration.
5. Start the new Reach service.
6. Validate `/healthz`, `/api/v1/summary`, MCP discovery and one representative short execution.
7. Validate asynchronous execution when the release changes execution lifecycle code.

Runs, Tasks and Candidates live in configured workspace paths and are independent of package installation.

A rollback across a Run schema change requires a compatible reader or an explicit data downgrade/quarantine step. Do not assume an older image can read newer Run records.

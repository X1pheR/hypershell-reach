# Repository Agent Guide

This repository contains the generic HATS product. Keep private deployment configuration, credentials, private host details and deployment-specific policy outside this repository.

## Repository rules

1. Prefer the smallest maintainable design that preserves typed configuration and explicit extension points.
2. Reuse an existing MCP capability, HATS tool or narrow extension point before adding reusable machinery.
3. Follow [`docs/tooling-lifecycle.md`](docs/tooling-lifecycle.md) before adding a reusable tool, script or helper.
4. Use `private deployment` for deployment-owned extensions in generic documentation and contracts.
5. Preserve established public IDs, configuration keys, schema fields and command names unless a change intentionally versions the contract.
6. Add focused tests and update owning documentation with behavioral changes.
7. Never add secret values, private keys, access tokens or private deployment-only data to examples or fixtures.
8. Run `uv run --frozen --extra dev pytest` before committing behavioral changes.
9. Follow [`docs/releasing.md`](docs/releasing.md) for versioned releases.

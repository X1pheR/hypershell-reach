# Development

Hypershell Reach uses Python 3.12+, `uv`, Pydantic and the Python MCP SDK.

## Setup

```bash
uv sync --extra dev
```

## Tests

```bash
uv run --frozen --extra dev pytest
```

Browser acceptance uses one repository-owned entrypoint:

```bash
bash scripts/ci-browser.sh
```

The browser harness builds the current checkout, starts Hypershell Reach UI and Playwright as sibling containers on an ephemeral Docker network, and reaches the UI by container DNS. It does not depend on host networking, loopback crossing container boundaries or host-visible checkout bind mounts. This makes the same entrypoint usable from a normal runner or from an explicitly Docker-enabled nested orchestrator.

Do not wrap this entrypoint in another ad-hoc container topology. If an execution environment cannot provide the Docker contract required by the script, treat that environment as unsupported instead of approximating CI with different networking or mount semantics.

## Repository structure

```text
.
├── docs/
├── examples/
├── src/hypershell_reach/
│   ├── bundled_tools/
│   ├── cli.py
│   ├── config.py
│   ├── execution.py
│   ├── managed_tools.py
│   ├── read_model.py
│   ├── runs.py
│   ├── server.py
│   ├── skills.py
│   ├── tasks.py
│   ├── ui.py
│   └── validation.py
└── tests/
```

Add a module only when its responsibility is implemented. Keep deployment-specific examples as placeholders.

## Documentation

Use plain technical language. Keep the root README short and route detailed contracts to `docs/`.

Use Mermaid for architecture, lifecycle or sequence diagrams when it is clearer than prose. Do not use diagrams as decoration.

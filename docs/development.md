# Development

HATS uses Python 3.12+, `uv`, Pydantic and the Python MCP SDK.

## Setup

```bash
uv sync --extra dev
```

## Tests

```bash
uv run --frozen --extra dev pytest
```

## Repository structure

```text
.
├── docs/
├── examples/
├── src/hats_mcp/
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

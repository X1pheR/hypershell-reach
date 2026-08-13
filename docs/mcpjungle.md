# MCPJungle

HATS does not depend on MCPJungle. Any MCP client that can launch a STDIO server can run `hats-mcp`.

For MCPJungle, a minimal registration can look like:

```json
{
  "name": "hats",
  "transport": "stdio",
  "command": "hats-mcp",
  "env": {
    "HATS_CONFIG": "/config/hats.yaml"
  },
  "session_mode": "stateful"
}
```

The configuration path, workspace paths, tool/skill sources and SSH credential files are deployment-owned.

## Tool-group policy

Do not expose the complete HATS server to every consumer merely because it is registered. A gateway policy can expose:

- read-only skill discovery/retrieval to routine clients;
- target execution, managed tools and Run/Task mutations only to administrative clients.

HATS tool metadata does not replace gateway or agent authorization policy.

## Deployment source

For maintained deployment, prefer installing an exact verified HATS release artifact into the gateway runtime and execute `hats-mcp` directly. This avoids a runtime dependency on a mutable Git checkout.

Running from an exact clean checkout remains supported for development and transition deployments:

```bash
uv run --frozen --directory /path/to/hats-source hats-mcp
```

Any source-pin wrapper is deployment-specific and should remain outside the generic HATS repository when it contains local paths or policy.

## Persistent state

Keep configured Run and Task workspace paths outside ephemeral package/install directories. Stateful STDIO refers to the MCP child/session model; durable HATS continuity comes from its filesystem-backed workspace.

# MCPJungle

MCPJungle is a consumer/router of Hypershell Reach, not its runtime host. Reach runs independently as one long-lived service.

## Registration

Reach exposes a stateless Streamable HTTP MCP endpoint at `/mcp`. A deployment registration can use:

```json
{
  "name": "reach",
  "transport": "streamable_http",
  "description": "Hypershell Reach capability layer for governed agent tools, skills, execution, Runs and Tasks.",
  "url": "http://reach:8080/mcp",
  "session_mode": "stateless"
}
```

The exact service DNS name is deployment-owned. MCPJungle does not receive Reach SSH credentials or own Reach's Run/Task workspace.

## Tool-group policy

Registration does not imply universal exposure. Keep routine clients limited to the read/capability tools they need. Administrative groups may additionally receive execution and state-mutation tools.

Reach tool metadata does not replace gateway or caller authorization policy.

## Lifecycle

An MCPJungle request or upstream tunnel command may be cancelled independently of Reach. A `start_*` call that Reach already accepted continues because the execution manager belongs to the Reach service lifecycle, not the gateway request lifecycle.

If the Reach service itself stops, running asynchronous Runs are not replayed automatically. Startup reconciliation reports the interrupted boundary explicitly.

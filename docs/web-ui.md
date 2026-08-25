# Web UI and read-only API

The Web UI and read-only API are built into the main Hypershell Reach service. They are not separate deployments.

## Web UI

The UI provides read-only views for Overview, Targets, Tooling, Runs, Tasks, Skills and Documentation. It uses `ReachReadModel`, which opens Run and Task stores read-only and exposes only bounded summaries.

The browser surface does not show target connection addresses, SSH users, credential paths or values, command/script bodies, command output or full Task continuity.

## Read-only API

The same sanitized read model backs:

- `GET /api/v1/summary`;
- `GET /api/v1/skills`;
- `GET /api/v1/tools`;
- `GET /api/v1/tasks`;
- `GET /api/v1/runs?limit=N`.

`/api/v1/summary` is intended for small internal dashboard widgets. The inventory endpoints are read-only inspection surfaces, not administration APIs.

## Performance

Large Run workspaces use newest-first bounded reads rather than parsing every Run before applying a limit. Process-local caches reduce repeated filesystem/source scanning for Tasks, tools and skills. Runs remain near-live; only the total Run count receives a short cache.

## Runtime

Start the complete product service:

```bash
REACH_CONFIG=/path/to/reach.yaml reach --host 0.0.0.0 --port 8080
```

The maintained container exposes port `8080`. Authentication, TLS, DNS and external ingress remain deployment responsibilities.

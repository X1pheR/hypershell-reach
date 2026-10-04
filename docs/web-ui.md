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
- `GET /api/v1/runs?limit=N`;
- `GET /api/v1/candidates`.

`/api/v1/summary` is intended for small internal dashboard widgets. The inventory endpoints are read-only inspection surfaces, not administration APIs.

## Performance

Unified operational state uses indexed bounded server queries and exact counts. Read-only tools and skills retain bounded source caches. Legacy configurations remain supported.

## Runtime

Start the complete product service:

```bash
REACH_CONFIG=/path/to/reach.yaml reach --host 0.0.0.0 --port 8080
```

The maintained container exposes port `8080`. Authentication, TLS, DNS and external ingress remain deployment responsibilities.

## Indexed operational queries

With unified persistence, operational lists, counts and retention use SQL predicates over typed columns. HTTP inventory responses retain `count` and `items`, and expose `total`, `limit` and `offset`. `total` counts the filtered set, independently of the page. Existing Tasks default to 500 rows and Runs to 100; explicit limits are 1–500 and offsets 0–10,000,000.

| Endpoint | Filters | Sort fields |
|---|---|---|
| `/api/v1/runs` | `status`, `task_id`, `target`, `operation`, `execution_mode`, `execution_class`, `retained`, `ambiguous`, `started_after`, `started_before`, `ended_after`, `ended_before`, `q` | `id`, `started_at`, `ended_at`, `status`, `target`, `operation`, `execution_mode`, `execution_class` |
| `/api/v1/tasks` | `status`, `project_ref`, `retained`, `blocked`, `archived`, `q` | `id`, `title`, `status`, `updated_at`, `created_at`, `archived_at`, `project_ref` |
| `/api/v1/candidates` | `status`, `owner_id`, `q` | `id`, `title`, `status`, `updated_at`, `created_at`, `recurrence_count` |

Use `sort` and `dir=asc|desc`; booleans are `true|false`. Tasks also accept `archived=all`, while the default remains active storage. Time filters require timezone-aware timestamps. Invalid recognized parameters return 400. An unconfigured Candidate endpoint returns `configured=false` and no records; it does not enable storage.

`/api/v1/summary` adds uncapped operational counts for archived, active and blocked Tasks, running/ambiguous/error Runs. The server-rendered tables and Task-related Run history page over the same read model; no client SQLite access or mutation endpoints are added. Text search is bounded parameterized search, not a claim of a full-text analytics index.

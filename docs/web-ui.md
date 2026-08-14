# Web UI

HATS includes an optional read-only HTTP runtime named `hats-ui`. It is delivered from the same source repository and release as `hats-mcp`, but runs as a separate process or container.

## Purpose

The first UI provides operational visibility without becoming another HATS writer. It exposes server-rendered views for:

- Targets;
- Managed tooling;
- Runs;
- Tasks;
- Skills;
- Tooling Candidates.

The UI reuses the existing HATS configuration, domain models and file-backed stores. `RunStore` and `TaskStore` are opened in explicit read-only mode, so the UI does not reconcile runs, perform retention cleanup, create tasks or update persisted state.

## Information boundary

The UI renders only bounded summaries. It does not render target connection addresses, SSH users, credential paths, credential values, managed-script source code, run command text, run output content or full task-continuity evidence.

Hermes skill content can be scanned from a configured read-only filesystem source. The UI does not perform SSH calls to project Hermes' live effective enable/disable state. `hats-mcp` remains the authority for the live effective skill catalog.

## Runtime

Install the exact HATS release wheel; the same artifact provides both entrypoints:

```bash
uv tool install ./homelab_agent_tooling_skills_mcp-X.Y.Z-py3-none-any.whl
```

Start the HTTP runtime with the same deployment configuration used by HATS:

```bash
HATS_CONFIG=/path/to/hats.yaml hats-ui --host 127.0.0.1 --port 8080
```

The default bind is loopback. A deployment that exposes the UI through a reverse proxy should keep authentication, TLS, DNS and ingress policy outside the generic HATS product.

The repository Dockerfile packages the UI role and listens on container port `8080`. It does not replace the existing `hats-mcp` STDIO deployment contract.

## HTTP surface

The UI serves only read-oriented routes:

- `/`
- `/targets`
- `/tooling`
- `/runs`
- `/tasks`
- `/skills`
- `/candidates`
- `/healthz`

HTML responses disable caching and send defensive content, framing and referrer headers. Mutating HTTP methods are not implemented.

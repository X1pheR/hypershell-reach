# Web UI

HATS includes an optional read-only HTTP runtime named `hats-ui`. It is delivered from the same source repository and release as `hats-mcp`, but runs as a separate process or container.

## Purpose

The Web UI provides browser-based visibility into HATS without becoming another writer for HATS state. Its primary destinations are:

- Overview;
- Targets;
- Tooling;
- Runs;
- Tasks;
- Skills;
- Documentation.

Tooling contains both registered managed tools and reviewed tooling candidates. Documentation separates a plain-language user guide from curated technical documentation.

The UI reuses the existing HATS configuration, domain models and file-backed stores. `RunStore` and `TaskStore` are opened in explicit read-only mode, so the UI does not reconcile runs, perform retention cleanup, create tasks or update persisted state.

## Documentation model

The user guide is maintained in [`user-guide.md`](user-guide.md).

Technical pages are rendered from the existing repository Markdown files. They are packaged into the release wheel and container image rather than copied into a second HTML documentation set. This keeps architecture, security, installation, configuration, operations, skills, tools, development and release guidance under one source of truth.

Markdown is rendered with raw HTML disabled. Only an explicit curated list of repository documents is addressable through the technical documentation routes.

## Information boundary

The UI renders only bounded summaries. It does not render target connection addresses, SSH users, credential paths, credential values, managed-script source code, run command text, run output content or full task-continuity evidence.

Hermes skill content can be scanned from a configured read-only filesystem source. The UI does not perform SSH calls to project Hermes' live effective enable/disable state. `hats-mcp` remains the authority for the live effective skill catalog.

## Application shell

The maintained UI uses a compact top application bar with the HATS identity, centered primary destinations and a visible read-only utility state. Smaller viewports replace the desktop destinations with one off-canvas navigation dialog using the same information architecture.

Pages use bounded content regions, native tables for repeated data and page-local documentation navigation. Status meaning is written as text and is never conveyed only through color.

The shell includes a skip link, visible keyboard focus, reduced-motion handling, forced-colors fallbacks and responsive layouts that keep horizontal overflow inside intentionally scrollable data regions.

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

The UI serves read-oriented routes:

- `/`
- `/targets`
- `/tooling`
- `/runs`
- `/tasks`
- `/skills`
- `/docs`
- `/docs/technical/{slug}` for curated technical pages
- `/healthz`

`/candidates` remains a compatibility redirect to the Tooling candidates section.

The UI also serves its local stylesheet and navigation script under `/assets/`. HTML responses disable caching and use a restrictive Content Security Policy that permits only same-origin stylesheet and script assets. Mutating HTTP methods are not implemented.

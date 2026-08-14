# Architecture

HATS is a modular MCP service for reusable homelab agent capabilities. It keeps deployment configuration outside the repository and adds modules only when they solve a concrete use case.

## Overview

```mermaid
flowchart LR
    Client[MCP client] --> Server[HATS MCP server]
    Config[YAML configuration] --> Server
    Server --> Targets[Target registry]
    Server --> Tools[Managed tool registry]
    Targets --> SSH[SSH execution]
    Tools --> SSH
    Server --> Runs[Run state]
    Server --> Tasks[Task state]
    Server --> Skills[Skill sources]
    Server --> Registry[Optional tooling registry]
```

One process owns configuration, target lookup, tool discovery and local state. HATS does not require a separate controller, database server or worker service.

## Modules

| Module | Responsibility |
| --- | --- |
| Configuration | Validate deployment-specific targets, source roots and workspace paths. |
| Targets | Return safe target metadata and resolve one configured execution target. |
| SSH execution | Run bounded non-interactive commands or interpreter input on configured targets. |
| Managed tools | Discover approved metadata and execute only registered script IDs. |
| Runs | Record technical execution state and ambiguous/interrupted outcomes. |
| Tasks | Keep durable continuity only for work that needs recovery or handoff. |
| Skills | Discover and retrieve read-only Agent Skills from configured sources. |
| Tooling registry | Validate an optional deployment-owned registry and derive explicit promotion candidates without a second candidate store. |

## Managed tool execution

```mermaid
sequenceDiagram
    participant C as MCP client
    participant H as HATS
    participant S as Tool source
    participant T as Target
    C->>H: run_script(script_id, target, arguments)
    H->>S: Resolve exact registered ID
    H->>H: Validate metadata, arguments, capabilities
    H->>T: Send script over bounded SSH stdin
    T-->>H: Exit status and bounded output
    H-->>C: Structured result
```

Tool source delivery is external to HATS. A target does not need the source repository or script installed locally.

## Delivery phases

```mermaid
flowchart TD
    P1[1. Targets and bounded execution] --> P2[2. Managed tools]
    P2 --> P3[3. Runs and tasks]
    P3 --> P4[4. Private homelab tool migration]
    P4 --> P5[5. Skills and Hermes compatibility]
    P5 --> P6[6. Public readiness]
```

Each phase must leave the service usable. Later modules must not weaken the execution boundary established in phase 1.

## Source boundary

The HATS repository owns generic product code, schemas, examples and tests. Deployment-specific non-secret configuration, target inventory and private managed-tool or skill sources stay outside the product repository and should normally be owned by the deployment's existing private infrastructure or configuration source.

A deployment does not need a dedicated private HATS overlay repository merely to keep configuration private. Use a separate private repository only when an independent lifecycle, ownership or security boundary requires one.

```mermaid
flowchart LR
    Product[HATS product repository] --> Service[HATS service]
    Deployment[Private deployment configuration] --> Service
    PrivateTools[Private tool source] --> Service
    Hermes[Hermes active skills] --> Service
    LocalSkills[Other skill source] --> Service
```

HATS consumes configured filesystem sources. It does not own their Git, rsync or mount lifecycle.

## Optional web UI

HATS includes an optional Web UI in the same product repository and release rather than as a separate product. The MCP and UI are separate runtime roles:

```text
hats-mcp   # STDIO MCP entrypoint, for example as an MCP gateway child
hats-ui    # optional HTTP entrypoint in a separate long-running container/process
```

The first UI is read-only and reuses the HATS domain models and configured state through explicit read-only store access. It does not reconcile Runs, perform retention cleanup, mutate Tasks or project remote Hermes state. Do not run a long-lived HTTP listener as an incidental child of a STDIO gateway process. If browser mutations are added later, establish one explicit writer/service boundary instead of letting independent MCP and UI processes mutate the same file-backed state concurrently.

## Execution boundary

The caller selects a configured target. Target configuration owns host, port, user, identity, known-hosts file and execution limits.

SSH execution is non-interactive and uses strict host-key checking, key-only authentication, no PTY, no forwarding, bounded output, bounded timeout and no automatic retry.

A failed or interrupted mutation can leave remote state ambiguous. HATS must report that ambiguity; it must not claim rollback or safe retry without evidence.

## Skills boundary

Skills are read-only instructions and supporting files. A script stored inside a skill package is still skill content. It does not become a managed HATS tool automatically.

The Hermes source adapter mirrors Hermes' effective skill catalog rather than only scanning directories. This includes enable/disable state and supported provenance while keeping Hermes as the authority for its own skills.

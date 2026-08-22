# HATS User Guide

## What HATS is

HATS stands for **Homelab Agent Tooling & Skills**. It gives agents controlled access to homelab systems, reusable tools, run history, cross-session task state and shared Agent Skills.

The Web UI is a **read-only companion** to the MCP service. Use it to see what HATS knows and what happened recently. The UI does not authorize or start remote actions, edit configuration, create tasks or change retained state.

## Overview

The Overview is the starting point. It summarizes configured Targets, managed Tooling, recent Runs, active Tasks and available Skills, and links to each detailed view. It also links to Documentation.

Use it to spot unavailable sources or recent failures before opening a detailed view.

## Targets

Targets are the configured systems HATS can connect to through its controlled execution path.

The Targets view shows safe metadata such as:

- the target ID and display name;
- the transport type;
- declared capabilities;
- whether the target is enabled;
- execution timeout and output limits.

Connection addresses, SSH usernames, credential paths and credential values are deliberately omitted from the UI.

## Tooling

Tooling contains two related sections.

### Managed tools

Managed tools are registered, reviewed actions that HATS can run through its managed-tool path. The UI shows their safe metadata, including purpose, source, domain, interpreter, requirements, whether they can change state and whether repeated execution is expected to be safe.

The UI does not display tool source code or deployment filesystem paths.

### Tooling candidates

Tooling candidates are recurring gaps recorded for possible reusable automation. A candidate is **not** automatically an executable tool. It still needs normal review and implementation before it can become a managed tool.

Keeping candidates beside Managed tools shows what may be automated next without creating a separate product area.

## Runs

A Run is the stored summary of one HATS execution attempt. The Runs view helps answer questions such as:

- what operation was attempted;
- which configured target it used;
- whether it belonged to a Task or managed tool;
- whether it succeeded, failed, timed out or ended ambiguously;
- when it started and ended;
- whether the record is retained.

HATS does not persist command bodies, argument values or command output in the Run record, so the Web UI cannot expose them later.

## Tasks

A Task keeps continuity for substantial work that may span sessions or be interrupted. It is not a project-management system and it does not grant permission to perform changes.

The Tasks view intentionally shows only task ID, title, state, update time and retention state. The full continuity snapshot can contain operational context that does not belong in the browser UI.

## Skills

Skills are read-only Agent Skill packages that help agents follow reusable workflows and conventions.

The Skills view shows configured skill content and where it came from. For sources that need a live adapter to determine effective enablement, the browser may show readable content without claiming that the skill is currently active. The `hats-mcp` skill tools remain authoritative for the live effective catalog.

## Help

Help has two layers:

- **User guide** — explains the product in plain language and how to interpret the Web UI;
- **Technical reference** — the maintained repository documentation for architecture, security, installation, configuration, operations, tools, skills, development and releases.

The technical pages shown in the UI are rendered from the same Markdown files maintained in the repository. They are not a second copied documentation set.

## Read-only and privacy boundary

The Web UI is intentionally narrower than the admin MCP surface. Browser access does not add execution capability.

The UI does not render:

- target connection addresses or SSH usernames;
- credential paths or secret values;
- managed-tool source code;
- command text, argument values or command output;
- full Task continuity state.

Authentication, TLS, DNS and ingress policy are deployment concerns outside the generic HATS product.

## Understanding states

Status styling is an aid, not the only signal. Every state is also written as text.

- **Success/completed** means the recorded operation or state completed successfully.
- **Running/warning/partial/blocked** means work is active, incomplete or requires attention.
- **Failed/error/timeout/interrupted** means the recorded operation did not complete successfully.
- **Unknown/neutral** means the UI does not have enough information to claim success or failure.

A reachable target or available browser page is not by itself proof that a complete workflow is healthy.

## Common questions

### Can I run a command from the Web UI?

No. The maintained HATS Web UI is read-only. Execution remains on the MCP/admin path and still depends on caller authorization.

### Can I edit HATS configuration in the Web UI?

No. Deployment-owned YAML and related desired state remain the configuration authority.

### Why can I not see a target address or credential path?

Those fields are intentionally hidden from the browser. The UI only needs enough information to identify the target and show what it supports.

### Why does a skill appear here when it may not be active in an agent?

Some skill sources can be read as files without knowing their current effective enable/disable state. The MCP skill catalog is authoritative when live activation matters.

### Where do I find installation or configuration details?

Open **Help → Technical reference** and select Installation or Configuration.

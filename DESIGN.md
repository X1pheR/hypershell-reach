---
version: alpha
name: HATS
description: Small read-only server-rendered Hypershell interface for agent tooling and skills, with no writer or SPA boundary.
colors:
  primary: "#3C6CFE"
  secondary: "#FF2093"
  tertiary: "#22D3EE"
  page: "#050816"
  surface: "#0B1020"
  surface-raised: "#0F172A"
  surface-soft: "#131D31"
  text: "#E2E8F0"
  heading: "#F1F5F9"
  muted: "#94A3B8"
  border: "#4C5C80"
  focus: "#67E8F9"
  structural-cyan: "#78AEB9"
  structural-pink: "#B97599"
  success: "#6EE7A8"
  warning: "#F3C96B"
  error: "#FF8585"
  scrollbar-track: "#070B17"
  scrollbar-thumb: "#33415F"
  scrollbar-thumb-hover: "#506284"
typography:
  h1:
    fontFamily: Inter, ui-sans-serif, system-ui, sans-serif
    fontSize: 2.25rem
    fontWeight: 760
    lineHeight: 1.15
    letterSpacing: "-0.04em"
  h2:
    fontFamily: Inter, ui-sans-serif, system-ui, sans-serif
    fontSize: 1.5rem
    fontWeight: 720
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  body-md:
    fontFamily: Inter, ui-sans-serif, system-ui, sans-serif
    fontSize: 1rem
    fontWeight: 400
    lineHeight: 1.5
  body-sm:
    fontFamily: Inter, ui-sans-serif, system-ui, sans-serif
    fontSize: 0.875rem
    fontWeight: 400
    lineHeight: 1.45
  label-sm:
    fontFamily: Inter, ui-sans-serif, system-ui, sans-serif
    fontSize: 0.8125rem
    fontWeight: 650
    lineHeight: 1.35
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  2xl: 32px
  3xl: 40px
rounded:
  sm: 8px
  md: 11px
  lg: 18px
  xl: 20px
  pill: 999px
components:
  surface-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
    padding: "{spacing.xl}"
  surface-raised:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "{spacing.lg}"
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.page}"
    rounded: "{rounded.md}"
    padding: "{spacing.md}"
    height: 44px
  button-secondary:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "{spacing.md}"
    height: 44px
  input:
    backgroundColor: "{colors.page}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "{spacing.md}"
    height: 44px
  select:
    backgroundColor: "{colors.surface-soft}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "{spacing.md}"
    height: 44px
  status-success:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.success}"
    rounded: "{rounded.pill}"
    padding: "{spacing.sm}"
  status-warning:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.warning}"
    rounded: "{rounded.pill}"
    padding: "{spacing.sm}"
  status-error:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.error}"
    rounded: "{rounded.pill}"
    padding: "{spacing.sm}"
---

# HATS Design

## Overview

This file is the repository-local projection of the canonical Hypershell application-family visual contract at `_meta/operating-models/web-ui/DESIGN.md`. Shared family tokens and equivalent interaction recipes are synchronized deliberately; this repository owns only HATS-specific product decisions.

HATS stays small, read-only, server-rendered and operational. Family conformance does not justify a SPA, frontend framework, client-side state store, configuration writer, global-search subsystem or broader information-exposure boundary.

## Colors

Use the shared dark surface hierarchy and restrained pink-blue-cyan interaction identity from the front matter. Operational state uses semantic colors and never relies on color alone.

## Typography

Use compact application-scale headings and human-readable first-layer values. Keep exact target IDs, tool IDs, revisions and other technical identifiers available where they matter.

## Layout

Keep the compact server-rendered topbar and routeable primary destinations. At 320, 360 and 390 CSS px, desktop navigation yields to the left off-canvas navigation without page-level horizontal overflow. Help may retain its own bounded in-content navigation because curated repository documentation is a content-navigation need rather than a second application shell.

## Components

**Brand lockup.** Use `Hypershell` as the primary line and `HATS` as the secondary product line. Preserve both lines on mobile.

**Product mark.** Retain the hat silhouette with three connected nodes. The mark identifies HATS and is not reused as a generic shell-action icon.

**Capability versus runtime.** `Read-only` is a capability/mode badge. It is not runtime health. Runtime/dependency availability must be communicated separately when operationally meaningful.

**Tables.** Runs, Skills and Tooling may add proportionate search/filter/sort and bounded loading or pagination as growth requires. Do not import HomeSight's dense-grid machinery solely for parity.

**Help.** Global Help, contextual Help and application tooltips are separate roles. Repository Markdown remains the technical documentation source; UI rendering must keep its existing allowlisted/safe-document boundary.

## Do's and Don'ts

- Do preserve the read-only server-rendered architecture.
- Do keep major destinations routeable and Back/Forward meaningful.
- Do separate `Read-only` capability from runtime availability.
- Do keep documentation rendering bounded and safe.
- Don't add a SPA or frontend framework solely for harmonization.
- Don't add a writer or new configuration authority.
- Don't broaden exposed target/private information to make the UI appear richer.

# Skills

HATS will expose Agent Skills through progressive discovery. Skills remain read-only content and do not gain execution privileges.

## MCP surface

The planned surface is:

```text
skills_catalog()
skill_get(skill_id)
skill_read_file(skill_id, relative_path, ...)
```

The catalog returns compact metadata. Full `SKILL.md` content and supporting files are loaded only when selected.

## Sources

HATS supports multiple configured skill sources with stable source-qualified IDs.

```text
hermes:hypershell-vault-governance
local:my-skill
```

Unknown frontmatter fields are preserved. HATS validates the core skill contract without rejecting compatible extensions.

## Hermes compatibility

The Hermes adapter must mirror Hermes' effective skill catalog, not only the files present on disk.

For Hermes Agent 0.20.0 this means accounting for:

- the active `~/.hermes/skills` tree;
- platform compatibility;
- enable/disable state, including platform-specific disabled state;
- installed provenance;
- supporting `references`, `templates`, `assets` and `scripts` directories;
- external skill directories when configured;
- supported plugin or organization skill sources when they are active.

Content can be read from an approved read-only filesystem source. Effective state should use a narrow Hermes projection where possible instead of exposing the complete Hermes configuration to HATS.

## Supporting files

Supporting files can include Markdown, code, templates, schemas and binary assets. Large content must use bounded retrieval with explicit continuation metadata.

A script inside a skill package is not automatically registered as an executable HATS tool.

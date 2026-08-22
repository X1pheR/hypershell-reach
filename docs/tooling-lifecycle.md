# Tooling Lifecycle

HATS should reduce repeated operational friction without turning every incident into permanent machinery.

## Intake flow

1. Describe the observed failure or friction and its reproducible signature.
2. Fix isolated mistakes locally without adding reusable machinery.
3. If the issue is recurring or generalizable, record evidence in the deployment's failure registry or equivalent governed evidence store.
4. Check whether an existing structured MCP capability already solves the problem.
5. Check whether an existing HATS managed tool or configured private deployment tool solves it.
6. Check whether the narrow owning application or domain already provides a suitable operator helper.
7. Extend the narrowest existing capability when that remains simpler than adding another tool.
8. Create a new reusable tool or helper only when the earlier options are insufficient.
9. Add tests, documentation, safety metadata and deterministic postconditions.
10. Reclassify the recorded gap after acceptance so future agents know whether it is still observed, guarded, automated or retired.

## Ownership

- Generic reusable agent capability belongs in the HATS product repository.
- Deployment-specific non-secret configuration, target inventory, source bindings and private managed-tool extensions belong by default in the deployment's existing private infrastructure or configuration source.
- Do not create a dedicated private HATS overlay repository solely to keep deployment configuration private. Use a separate private repository only when an independent lifecycle, ownership or security boundary requires one.
- Application-specific behavior belongs in the application repository.
- Domain operator helpers belong with the domain or infrastructure source that owns their lifecycle.
- Governance validators belong with the governance source they validate.

Use `private deployment` rather than a project-specific deployment name in generic contracts and documentation.

## Promotion criteria

A recorded gap is a strong promotion candidate when all of these are true:

- the same failure or workaround has occurred more than once, or there is strong evidence it will recur;
- the cause is understood well enough to prevent rather than merely mask it;
- the lesson applies beyond one typo, malformed command or temporary incident;
- a deterministic preflight, response or postcondition can be defined;
- no existing structured capability already provides the required behavior;
- the narrow owner is clear;
- automation reduces meaningful risk, repetition or ambiguity rather than only saving a few keystrokes.

Promotion remains a reviewed decision. A registry can surface candidates automatically, but it should not automatically implement every candidate.

## Candidate lifecycle

HATS owns Candidate **state mechanics**, not source-code generation, Git workflow, or authorization policy. Candidate records are structured YAML state when `workspace.candidates` is configured; managed-tool source remains in the owning product or deployment repository.

A Candidate must preserve enough intent to survive chat loss: recurring problem, cause and evidence; proposed capability and optional managed-tool ID; required inputs and expected outputs; safety/mutation boundary; stable owner ID; deterministic acceptance postconditions; promotion rationale; and optional implementation Task/final capability references. Candidate state must not contain credential or secret values.

The state set is intentionally small: `candidate`, `approved`, `blocked`, `not-warranted`, `implemented`, and `automated`. `approved` means an operator has explicitly authorized implementation. The existence of `approve_candidate` never grants that authorization; callers must establish it outside HATS before invoking the transition. Generic `update_candidate` cannot change lifecycle state.

Every mutation uses an `expected_revision` CAS. Candidate writes are serialized by a per-Candidate interprocess lock, written to a same-directory temporary file, file-fsynced, atomically replaced, and followed by parent-directory fsync. A stale writer fails rather than silently overwriting committed state.

After approval, `link_candidate_task` can reference an existing HATS Task. Completion records either `implemented` or `automated` plus one stable final `managed-tool` or `capability` reference. Managed-tool references are checked against the effective ToolRegistry before completion. HATS does not persist physical implementation paths when stable owner/tool identifiers are sufficient.

## Legacy tooling-registry compatibility

The optional deployment-owned Markdown tooling registry remains a read-only compatibility feed. `tooling_candidates` preserves its current consumer contract. `preview_candidate_imports` maps only facts explicitly present in that feed to `candidate-v1` drafts and reports every required target field that remains unknown. It never mutates Candidate state and never invents missing facts. Malformed legacy entries fail safe.

A deployment may retire the compatibility feed only after its explicit candidates have been enriched into valid Candidate records, repository tests pass, and a separate governed deployment migration gate is reached.

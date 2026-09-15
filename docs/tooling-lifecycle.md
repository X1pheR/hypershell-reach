# Tooling Lifecycle

Hypershell Reach should capture material automation opportunities early without turning every observation into permanent machinery.

## Intake flow

1. Describe the observed failure, friction or manual reasoning path and its reproducible signature.
2. Check whether an existing structured MCP capability, Reach managed tool or narrow owner already solves it. A missed existing path is not a new Candidate.
3. Check the current structured Candidates for the same underlying gap.
4. If the gap is already represented, record exactly one new distinct occurrence with `record_candidate_occurrence`; re-reviewing the same incident does not increment it.
5. If the gap is material and not yet represented, create a Candidate immediately. The first observed occurrence is `recurrence_count: 1`; recurrence is evidence, never an intake gate.
6. Fix isolated mistakes locally when appropriate, but preserve a separately useful automation opportunity as a Candidate even when the immediate incident is already resolved.
7. During later review, prefer extending the narrowest existing capability over adding another tool. Create a new reusable tool or helper only when existing owners are insufficient.
8. Add tests, documentation, safety metadata and deterministic postconditions before implementation is accepted.
9. Reclassify the Candidate after review/acceptance so future agents can distinguish proposed, approved, blocked, not-warranted, implemented and automated work.

## Ownership

- Generic reusable agent capability belongs in the Hypershell Reach product repository.
- Deployment-specific non-secret configuration, target inventory, source bindings and private managed-tool extensions belong by default in the deployment's existing private infrastructure or configuration source.
- Do not create a dedicated private Hypershell Reach overlay repository solely to keep deployment configuration private. Use a separate private repository only when an independent lifecycle, ownership or security boundary requires one.
- Application-specific behavior belongs in the application repository.
- Domain operator helpers belong with the domain or infrastructure source that owns their lifecycle.
- Governance validators belong with the governance source they validate.

Use `private deployment` rather than a project-specific deployment name in generic contracts and documentation.

## Promotion criteria

A recorded Candidate is strong enough for implementation review when all of these are true:

- the cause is understood well enough to prevent rather than merely mask it;
- the lesson applies beyond one typo, malformed command or temporary incident;
- a deterministic preflight, response or postcondition can be defined;
- no existing structured capability already provides the required behavior;
- the narrow owner is clear;
- automation reduces meaningful risk, repetition or ambiguity rather than only saving a few keystrokes.

`recurrence_count` is a prioritization signal, not a promotion threshold. A count of `1` may still justify implementation when the observed work is expensive, risky or highly generalizable; a high count makes repeated cost visible without forcing an arbitrary threshold.

Candidate capture may be automatic when the observation is material and sufficiently specified. Promotion remains a reviewed decision: creating or incrementing a Candidate never approves implementation.

## Candidate lifecycle

Hypershell Reach owns Candidate **state mechanics**, not source-code generation, Git workflow, or authorization policy. Candidate records are structured YAML state when `workspace.candidates` is configured; managed-tool source remains in the owning product or deployment repository.

A Candidate must preserve enough intent to survive chat loss: problem, cause and evidence; monotonic `recurrence_count`; proposed capability and optional managed-tool ID; required inputs and expected outputs; safety/mutation boundary; stable owner ID; deterministic acceptance postconditions; promotion rationale; and optional implementation Task/final capability references. Candidate state must not contain credential or secret values.

`recurrence_count` starts at `1` for a newly captured observed opportunity. Increment it exactly once for each later independently observed occurrence of the same underlying gap. Reopening a chat, rerunning closure, retrying a failed write or re-evaluating the same incident is not another occurrence. The optional legacy free-text `recurrence` field remains readable for existing Candidate v1 records but is not required for new intake and must not be converted into an invented historical count.

The state set is intentionally small: `candidate`, `approved`, `blocked`, `not-warranted`, `implemented`, and `automated`. `approved` means an operator has explicitly authorized implementation. The existence of `approve_candidate` never grants that authorization; callers must establish it outside Hypershell Reach before invoking the transition. Generic `update_candidate` cannot change lifecycle state.

Every mutation uses an `expected_revision` CAS. Candidate writes are serialized by a per-Candidate interprocess lock, written to a same-directory temporary file, file-fsynced, atomically replaced, and followed by parent-directory fsync. A stale writer fails rather than silently overwriting committed state.

After approval, `link_candidate_task` can reference an existing Hypershell Reach Task. Completion records either `implemented` or `automated` plus one stable final `managed-tool` or `capability` reference. Managed-tool references are checked against the effective ToolRegistry before completion. Hypershell Reach does not persist physical implementation paths when stable owner/tool identifiers are sufficient.

## Legacy tooling-registry compatibility

The optional deployment-owned Markdown tooling registry remains a read-only compatibility feed. `tooling_candidates` preserves its current consumer contract. `preview_candidate_imports` maps only facts explicitly present in that feed to `candidate-v1` drafts and reports every required target field that remains unknown. It never mutates Candidate state and never invents missing facts. Malformed legacy entries fail safe.

A deployment may retire the compatibility feed only after its explicit candidates have been enriched into valid Candidate records, repository tests pass, and a separate governed deployment migration gate is reached.

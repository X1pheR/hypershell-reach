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

- Generic reusable agent capability belongs in the HATS product.
- Private deployment-specific agent capability belongs in the private deployment extension source.
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

## Failure registry and candidates

Use one governed failure registry rather than duplicating evidence into a second candidate registry. Candidate status is a view over recorded failures and friction that meet the promotion criteria above.

A future HATS registry integration should own generic schema and read/write mechanics while private deployment entries remain deployment-owned. Read access can be broadly useful; mutation should be a typed, explicitly mutating operation rather than arbitrary document editing.

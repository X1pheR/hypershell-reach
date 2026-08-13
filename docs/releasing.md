# Release Lifecycle

HATS uses Semantic Versioning. While the project is on `0.x`, minor versions may contain intentional contract changes; patch versions should remain backward-compatible fixes.

## Release flow

1. Start from a clean `main` checkout that matches the remote branch.
2. Run `uv run --frozen --extra dev pytest` and require repository CI for the exact commit to pass.
3. Update the package version in `pyproject.toml` as part of the reviewed release commit.
4. Review user-facing changes, configuration compatibility, tool IDs and documented migration notes.
5. Create an annotated Git tag named `vX.Y.Z`; the tag version must match `pyproject.toml`.
6. Create the GitHub release from that exact tag with concise release notes and upgrade considerations.
7. Deployments select and pin an exact reviewed release or commit.
8. Validate the deployed revision through the deployment's normal acceptance checks.

## Publication boundary

Repository CI validates source and release commits. Production deployment remains a separate operator action. The repository is currently source-run and does not claim package-registry publication.

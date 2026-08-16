# Release Lifecycle

HATS uses Semantic Versioning. While the project is on `0.x`, minor versions may contain intentional contract changes; patch versions should remain backward-compatible fixes.

## Release flow

1. Start from a clean `main` checkout that matches the remote branch.
2. Run `uv run --frozen --extra dev pytest`, build the repository Dockerfile with the exact package version and Git revision, and require repository CI for the exact commit to pass:

   ```bash
   package_version=$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
   revision=$(git rev-parse HEAD)
   created=$(date -u -d "@$(git show -s --format=%ct HEAD)" +%Y-%m-%dT%H:%M:%SZ)
   docker build \
     --build-arg "HATS_VERSION=${package_version}" \
     --build-arg "HATS_REVISION=${revision}" \
     --build-arg "HATS_CREATED=${created}" \
     -t hats-ui:release-candidate .
   ```

   Verify the resulting OCI labels identify HATS and contain the same version and revision before accepting the image.
3. Update the package version in `pyproject.toml` as part of the reviewed release commit.
4. Review user-facing changes, configuration compatibility, tool IDs and documented migration notes.
5. Create an annotated Git tag named `vX.Y.Z`; the tag version must match `pyproject.toml`. Use the tag annotation as the concise release notes and include upgrade considerations when required.
6. Push only the accepted tag. The `Release` workflow validates the tag/version pair, reruns the exact-tag test suite, builds wheel and sdist with `SOURCE_DATE_EPOCH` fixed to the release commit timestamp, writes `SHA256SUMS`, and creates the GitHub release from that tag.
7. Treat the published tag and release assets as immutable. If release contents need correction, create a new version instead of replacing an accepted artifact.
8. Deployments select and pin an exact reviewed release or commit.
9. Validate the deployed revision through the deployment's normal acceptance checks.

## Publication boundary

Repository CI validates source commits. The tag-triggered `Release` workflow validates and publishes accepted GitHub releases. Production deployment remains a separate operator action. HATS does not claim publication to a Python package registry or container registry.

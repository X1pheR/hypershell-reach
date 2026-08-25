# Release Lifecycle

Hypershell Reach uses Semantic Versioning. While the project is on `0.x`, minor versions may contain intentional contract changes; patch versions should remain backward-compatible fixes.

## Release flow

1. Start from a clean `main` checkout that matches the remote branch.
2. Run `uv run --frozen --extra dev pytest` and `bash scripts/ci-browser.sh`, build the repository Dockerfile with the exact package version and Git revision, and require repository CI for the exact commit to pass. The browser acceptance entrypoint is the canonical topology: Hypershell Reach UI and Playwright run as sibling containers on an ephemeral Docker network and communicate by container DNS, without host-network or host-visible bind-mount assumptions:

   ```bash
   package_version=$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
   revision=$(git rev-parse HEAD)
   created=$(date -u -d "@$(git show -s --format=%ct HEAD)" +%Y-%m-%dT%H:%M:%SZ)
   docker build \
     --build-arg "REACH_VERSION=${package_version}" \
     --build-arg "REACH_REVISION=${revision}" \
     --build-arg "REACH_CREATED=${created}" \
     -t hypershell-reach:release-candidate .
   ```

   Verify the resulting OCI labels identify Hypershell Reach and contain the same version and revision before accepting the image.
3. Update the package version in `pyproject.toml` as part of the reviewed release commit.
4. Review user-facing changes, configuration compatibility, tool IDs and documented migration notes.
5. Create an annotated Git tag named `vX.Y.Z`; the tag version must match `pyproject.toml`. Use the tag annotation as the concise release notes and include upgrade considerations when required.
6. Push only the accepted tag. The `Release` workflow validates the tag/version pair, reruns the exact-tag test suite, builds wheel and sdist with `SOURCE_DATE_EPOCH` fixed to the release commit timestamp, writes `SHA256SUMS`, and creates the GitHub release from that tag.
7. Treat the published tag and release assets as immutable. If release contents need correction, create a new version instead of replacing an accepted artifact.
8. Deployments select and pin an exact reviewed release or commit.
9. Validate the deployed revision through the deployment's normal acceptance checks. When a release changes an MCP tool contract, do not accept the deployment until the deployed server schema, gateway-exposed schema and every maintained consumer surface that receives that tool all expose the expected contract. A server-side schema change alone is not sufficient evidence that an already-bound consumer refreshed its tool metadata.

## Publication boundary

Repository CI validates source commits. The tag-triggered `Release` workflow validates and publishes accepted GitHub releases. Production deployment remains a separate operator action. Hypershell Reach does not claim publication to a Python package registry or container registry.

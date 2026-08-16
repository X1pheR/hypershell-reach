from __future__ import annotations

from pathlib import Path


def test_dockerfile_sets_hats_oci_identity() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG HATS_VERSION" in dockerfile
    assert "ARG HATS_REVISION" in dockerfile
    assert "ARG HATS_CREATED" in dockerfile
    assert 'org.opencontainers.image.title="HATS"' in dockerfile
    assert 'org.opencontainers.image.source="https://github.com/X1pheR/homelab-agent-tooling-skills-mcp"' in dockerfile
    assert 'org.opencontainers.image.url="https://github.com/X1pheR/homelab-agent-tooling-skills-mcp"' in dockerfile
    assert 'org.opencontainers.image.licenses="MIT"' in dockerfile
    assert 'org.opencontainers.image.created="${HATS_CREATED}"' in dockerfile
    assert 'org.opencontainers.image.version="${HATS_VERSION}"' in dockerfile
    assert 'org.opencontainers.image.revision="${HATS_REVISION}"' in dockerfile

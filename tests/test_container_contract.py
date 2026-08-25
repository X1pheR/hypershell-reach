from __future__ import annotations

from pathlib import Path


def test_dockerfile_sets_reach_oci_identity() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG REACH_VERSION" in dockerfile
    assert "ARG REACH_REVISION" in dockerfile
    assert "ARG REACH_CREATED" in dockerfile
    assert 'org.opencontainers.image.title="Hypershell Reach"' in dockerfile
    assert 'org.opencontainers.image.source="https://github.com/X1pheR/hypershell-reach"' in dockerfile
    assert 'org.opencontainers.image.url="https://github.com/X1pheR/hypershell-reach"' in dockerfile
    assert 'org.opencontainers.image.licenses="MIT"' in dockerfile
    assert 'org.opencontainers.image.created="${REACH_CREATED}"' in dockerfile
    assert 'org.opencontainers.image.version="${REACH_VERSION}"' in dockerfile
    assert 'org.opencontainers.image.revision="${REACH_REVISION}"' in dockerfile


def test_unified_reach_image_installs_ssh_client() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert "openssh-client" in dockerfile

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hats_mcp.config import SkillSource
from hats_mcp.skills import HermesState, build_skill_registry, read_skill_file


def _skill(root: Path, relative: str, *, name: str, extra: str = "", body: str = "Body") -> Path:
    directory = root / relative
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: Description for {name}.\n{extra}---\n# {name}\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_filesystem_catalog_uses_qualified_ids_and_support_exclusion(tmp_path) -> None:
    _skill(tmp_path, "research/example", name="example")
    nested = tmp_path / "research/example/references/old"
    nested.mkdir(parents=True)
    _skill(tmp_path / "research/example/references", "old", name="old-copy")
    source = SkillSource(id="local", type="filesystem", path=str(tmp_path), os_platform="linux")

    registry = build_skill_registry([source])

    assert [skill.id for skill in registry.list()] == ["local:example"]
    assert registry.get("local:example").category == "research"


def test_catalog_description_matches_hermes_cap(tmp_path) -> None:
    directory = tmp_path / "long"
    directory.mkdir()
    raw_description = "x" * 1100
    (directory / "SKILL.md").write_text(
        f"---\nname: long\ndescription: {raw_description}\n---\n# Long\n",
        encoding="utf-8",
    )

    skill = build_skill_registry([SkillSource(id="local", path=str(tmp_path))]).get("local:long")

    assert len(skill.description) == 1024
    assert skill.description.endswith("...")
    assert len(skill.catalog_summary()["description"]) == 200
    assert skill.catalog_summary()["description"].endswith("...")
    assert skill.summary()["description"] == skill.description
    assert set(skill.catalog_summary()) == {"id", "name", "description", "category"}


def test_catalog_revision_changes_with_catalog_content(tmp_path) -> None:
    _skill(tmp_path, "one", name="one")
    source = SkillSource(id="local", path=str(tmp_path))
    first = build_skill_registry([source]).catalog_revision()

    _skill(tmp_path, "two", name="two")
    second = build_skill_registry([source]).catalog_revision()

    assert len(first) == 64
    assert len(second) == 64
    assert first != second

def test_platform_and_environment_filtering_for_filesystem_source(tmp_path) -> None:
    _skill(tmp_path, "mac", name="mac", extra="platforms: [macos]\n")
    _skill(tmp_path, "kanban", name="kanban", extra="environments: [kanban]\n")
    _skill(tmp_path, "normal", name="normal", extra="platforms: [linux]\n")
    source = SkillSource(id="local", path=str(tmp_path), os_platform="linux")

    registry = build_skill_registry([source])

    assert [skill.name for skill in registry.list()] == ["normal"]
    assert {skill.name for skill in registry.list(include_inactive=True)} == {"mac", "kanban", "normal"}


def test_hermes_effective_names_are_authoritative(tmp_path) -> None:
    _skill(tmp_path, "one", name="one")
    _skill(tmp_path, "two", name="two")
    (tmp_path / ".bundled_manifest").write_text("one:abc\n", encoding="utf-8")
    (tmp_path / ".hub").mkdir()
    (tmp_path / ".hub/lock.json").write_text(
        json.dumps(
            {
                "installed": {
                    "two": {
                        "source": "official",
                        "identifier": "official/two",
                        "trust_level": "builtin",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    source = SkillSource(
        id="hermes",
        type="hermes",
        path=str(tmp_path),
        os_platform="linux",
        state={
            "target": "hermes",
            "python_executable": "/usr/bin/python3",
            "config_path": "/tmp/config.yaml",
            "repo_path": "/tmp/hermes",
        },
    )
    state = HermesState(
        effective_names=frozenset({"one"}),
        disabled=frozenset({"two"}),
        external_dirs=(),
        consumer_platform="cli",
    )

    registry = build_skill_registry([source], {"hermes": state})

    assert [skill.id for skill in registry.list()] == ["hermes:one"]
    assert registry.get("hermes:one").provenance == "bundled"
    two = registry.get("hermes:two", require_effective=False)
    assert two.provenance == "hub"
    assert two.enabled is False
    with pytest.raises(ValueError, match="not active"):
        registry.get("hermes:two")


def test_missing_effective_hermes_content_fails_closed(tmp_path) -> None:
    _skill(tmp_path, "one", name="one")
    source = SkillSource(
        id="hermes",
        type="hermes",
        path=str(tmp_path),
        state={
            "target": "hermes",
            "python_executable": "/usr/bin/python3",
            "config_path": "/tmp/config.yaml",
            "repo_path": "/tmp/hermes",
        },
    )
    state = HermesState(
        effective_names=frozenset({"one", "plugin-skill"}),
        disabled=frozenset(),
        external_dirs=(),
        consumer_platform="cli",
    )

    with pytest.raises(RuntimeError, match="outside the configured content source"):
        build_skill_registry([source], {"hermes": state})


def test_support_file_reads_are_bounded_and_traversal_is_rejected(tmp_path) -> None:
    _skill(tmp_path, "example", name="example")
    support = tmp_path / "example/references/data.txt"
    support.parent.mkdir(parents=True)
    support.write_text("abcdefghij", encoding="utf-8")
    source = SkillSource(id="local", path=str(tmp_path))
    skill = build_skill_registry([source]).get("local:example")

    first = read_skill_file(skill, "references/data.txt", offset=0, max_bytes=4)
    second = read_skill_file(skill, "references/data.txt", offset=4, max_bytes=10)

    assert first["content"] == "abcd"
    assert first["truncated"] is True
    assert first["next_offset"] == 4
    assert second["content"] == "efghij"
    assert second["truncated"] is False
    with pytest.raises(ValueError):
        read_skill_file(skill, "../outside.txt")


def test_binary_file_returns_metadata_without_base64(tmp_path) -> None:
    _skill(tmp_path, "example", name="example")
    binary = tmp_path / "example/assets/file.bin"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\xff\x00\xfe")
    skill = build_skill_registry([SkillSource(id="local", path=str(tmp_path))]).get("local:example")

    result = read_skill_file(skill, "assets/file.bin")

    assert result["binary"] is True
    assert result["content"] is None
    assert result["returned_bytes"] == 3


def test_symlinked_skill_directory_is_rejected(tmp_path) -> None:
    outside = tmp_path / "outside"
    _skill(outside, "skill", name="linked")
    root = tmp_path / "root"
    root.mkdir()
    (root / "linked").symlink_to(outside / "skill", target_is_directory=True)

    with pytest.raises(ValueError, match="symlinked skill directories"):
        build_skill_registry([SkillSource(id="local", path=str(root))])

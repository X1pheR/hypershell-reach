from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypershell_reach.config import SkillSource
from hypershell_reach.skills import (
    HermesState,
    build_skill_registry,
    read_skill_file,
    skill_sources_config_signature,
    skill_sources_fingerprint,
)


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

    assert build_skill_registry([source]).catalog_revision() == first

    _skill(tmp_path, "two", name="two")
    second = build_skill_registry([source]).catalog_revision()

    assert len(first) == 64
    assert len(second) == 64
    assert first != second


def test_skill_freshness_keeps_catalog_revision_and_skill_sha_distinct(tmp_path) -> None:
    skill_md = _skill(tmp_path, "one", name="one", body="first body")
    source = SkillSource(id="local", path=str(tmp_path))
    first_registry = build_skill_registry([source])
    first_skill = first_registry.get("local:one")
    first_revision = first_registry.catalog_revision()
    first_fingerprint = skill_sources_fingerprint([source])

    skill_md.write_text(
        "---\nname: one\ndescription: Description for one.\n---\n# one\n\nsecond body\n",
        encoding="utf-8",
    )
    second_registry = build_skill_registry([source])
    second_skill = second_registry.get("local:one")

    assert second_registry.catalog_revision() == first_revision
    assert second_skill.sha256 != first_skill.sha256
    assert skill_sources_fingerprint([source]) != first_fingerprint


def test_support_file_change_changes_only_source_fingerprint(tmp_path) -> None:
    _skill(tmp_path, "one", name="one")
    support = tmp_path / "one/references/context.txt"
    support.parent.mkdir(parents=True)
    support.write_text("first", encoding="utf-8")
    source = SkillSource(id="local", path=str(tmp_path))
    first_registry = build_skill_registry([source])
    first_revision = first_registry.catalog_revision()
    first_sha = first_registry.get("local:one").sha256
    first_fingerprint = skill_sources_fingerprint([source])

    support.write_text("other", encoding="utf-8")
    second_registry = build_skill_registry([source])

    assert second_registry.catalog_revision() == first_revision
    assert second_registry.get("local:one").sha256 == first_sha
    assert skill_sources_fingerprint([source]) != first_fingerprint


def test_source_fingerprint_detects_add_remove_and_rename(tmp_path) -> None:
    _skill(tmp_path, "one", name="one")
    source = SkillSource(id="local", path=str(tmp_path))
    initial = skill_sources_fingerprint([source])

    _skill(tmp_path, "two", name="two")
    added = skill_sources_fingerprint([source])
    assert added != initial

    (tmp_path / "two").rename(tmp_path / "renamed")
    renamed = skill_sources_fingerprint([source])
    assert renamed != added

    for path in (tmp_path / "renamed").iterdir():
        path.unlink()
    (tmp_path / "renamed").rmdir()
    assert skill_sources_fingerprint([source]) == initial


def test_source_fingerprint_is_stable_when_only_metadata_changes(tmp_path) -> None:
    skill_md = _skill(tmp_path, "one", name="one")
    source = SkillSource(id="local", path=str(tmp_path))
    first = skill_sources_fingerprint([source])

    skill_md.chmod(0o600)

    assert skill_sources_fingerprint([source]) == first


def test_hermes_provenance_metadata_changes_source_fingerprint(tmp_path) -> None:
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
    first = skill_sources_fingerprint([source])

    (tmp_path / ".bundled_manifest").write_text("one:abc\n", encoding="utf-8")

    assert skill_sources_fingerprint([source]) != first


def test_configured_source_signature_is_deterministic_and_ordered(tmp_path) -> None:
    first = SkillSource(id="a", path=str(tmp_path / "a"), os_platform="linux")
    second = SkillSource(id="b", path=str(tmp_path / "b"), os_platform="linux")

    signature = skill_sources_config_signature([first, second])

    assert skill_sources_config_signature([first.model_copy(), second.model_copy()]) == signature
    assert skill_sources_config_signature([second, first]) != signature
    assert (
        skill_sources_config_signature([first.model_copy(update={"os_platform": "macos"}), second])
        != signature
    )


def test_same_skill_name_across_sources_remains_source_qualified(tmp_path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _skill(first_root, "shared", name="shared")
    _skill(second_root, "shared", name="shared")
    sources = [
        SkillSource(id="a", path=str(first_root)),
        SkillSource(id="b", path=str(second_root)),
    ]

    registry = build_skill_registry(sources)

    assert [skill.id for skill in registry.list()] == ["a:shared", "b:shared"]
    assert skill_sources_fingerprint(sources) == skill_sources_fingerprint(sources)


def test_duplicate_skill_name_within_source_still_fails_closed(tmp_path) -> None:
    _skill(tmp_path, "one", name="duplicate")
    _skill(tmp_path, "two", name="duplicate")

    with pytest.raises(ValueError, match="duplicate skill name in source local"):
        build_skill_registry([SkillSource(id="local", path=str(tmp_path))])

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

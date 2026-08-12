from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from hats_mcp.config import ToolSource
from hats_mcp.managed_tools import (
    build_script_command,
    ensure_target_compatible,
    load_tool_registry,
    validate_script_arguments,
)


def _write_script(path: Path, *, script_id: str = "system.echo") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""#!/usr/bin/env bash
# ---
# id: {script_id}
# name: Echo
# description: Echo one bounded message.
# domain: {script_id.split('.', 1)[0]}
# interpreter: bash
# requires: [linux]
# mutating: false
# idempotent: true
# timeout_seconds: 15
# arguments:
#   - name: message
#     type: string
#     required: true
#     max_length: 32
#   - name: count
#     type: integer
#     required: false
#     minimum: 1
#     maximum: 5
# ---
printf '%s\\n' \"$@\"
""",
        encoding="utf-8",
    )


def _source(source_id: str, path: Path) -> ToolSource:
    return ToolSource(id=source_id, path=str(path))


def test_registry_discovers_frontmatter_and_ignores_plain_scripts(tmp_path) -> None:
    _write_script(tmp_path / "system" / "echo.sh")
    (tmp_path / "plain.sh").write_text("#!/bin/sh\necho plain\n", encoding="utf-8")

    scripts = load_tool_registry([_source("local", tmp_path)]).list()

    assert len(scripts) == 1
    assert scripts[0].metadata.id == "system.echo"
    assert scripts[0].source_id == "local"
    assert scripts[0].relative_path == "system/echo.sh"
    assert scripts[0].metadata.required_capabilities() == ["bash", "linux"]


def test_duplicate_ids_across_sources_are_rejected(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_script(first / "echo.sh")
    _write_script(second / "echo.sh")

    with pytest.raises(ValueError, match="duplicate managed tool IDs"):
        load_tool_registry([_source("first", first), _source("second", second)])


def test_managed_tool_symlink_is_rejected(tmp_path) -> None:
    source = tmp_path / "source"
    outside = tmp_path / "outside.sh"
    _write_script(outside)
    source.mkdir()
    (source / "linked.sh").symlink_to(outside)

    with pytest.raises(ValueError, match="must not be a symlink"):
        load_tool_registry([_source("local", source)])


def test_typed_arguments_build_stable_quoted_argv(tmp_path) -> None:
    _write_script(tmp_path / "echo.sh")
    script = load_tool_registry([_source("local", tmp_path)]).get("system.echo")

    command = build_script_command(script, {"message": "hello world", "count": 2})

    assert shlex.split(command) == [
        "bash",
        "-s",
        "--",
        "--message",
        "hello world",
        "--count",
        "2",
    ]


def test_argument_contract_rejects_missing_unknown_and_invalid_values(tmp_path) -> None:
    _write_script(tmp_path / "echo.sh")
    script = load_tool_registry([_source("local", tmp_path)]).get("system.echo")

    with pytest.raises(ValueError, match="missing arguments"):
        validate_script_arguments(script, {})
    with pytest.raises(ValueError, match="unknown arguments"):
        validate_script_arguments(script, {"message": "ok", "extra": "no"})
    with pytest.raises(ValueError, match="must be an integer"):
        validate_script_arguments(script, {"message": "ok", "count": "2"})
    with pytest.raises(ValueError, match="exceeds maximum"):
        validate_script_arguments(script, {"message": "ok", "count": 6})


def test_target_capabilities_are_checked_before_execution(tmp_path) -> None:
    _write_script(tmp_path / "echo.sh")
    script = load_tool_registry([_source("local", tmp_path)]).get("system.echo")

    ensure_target_compatible(script, ["linux", "bash"])
    with pytest.raises(ValueError, match="missing capabilities"):
        ensure_target_compatible(script, ["linux"])

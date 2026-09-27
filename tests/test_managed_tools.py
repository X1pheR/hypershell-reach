from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from hypershell_reach.config import ToolSource
from hypershell_reach.managed_tools import (
    ArgumentSpec,
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


def test_string_list_arguments_repeat_the_same_flag(tmp_path) -> None:
    script_path = tmp_path / "inspect.py"
    script_path.write_text(
        """#!/usr/bin/env python3
# ---
# id: filesystem.inspect
# name: Inspect paths
# description: Inspect explicit paths.
# domain: filesystem
# interpreter: python3
# mutating: false
# idempotent: true
# arguments:
#   - name: path
#     type: string_list
#     pattern: '^/[^\\r\\n]+$'
#     min_items: 1
#     max_items: 3
# ---
""",
        encoding="utf-8",
    )
    script = load_tool_registry([_source("local", tmp_path)]).get("filesystem.inspect")

    command = build_script_command(script, {"path": ["/srv/one", "/srv/two"]})

    assert shlex.split(command) == [
        "python3",
        "-",
        "--path",
        "/srv/one",
        "--path",
        "/srv/two",
    ]
    with pytest.raises(ValueError, match="list of strings"):
        validate_script_arguments(script, {"path": "/srv/one"})
    with pytest.raises(ValueError, match="exceeds max_items"):
        validate_script_arguments(script, {"path": ["/1", "/2", "/3", "/4"]})


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


def _number_script(tmp_path):
    path = tmp_path / "number.py"
    path.write_text(
        """# ---
# id: example.number
# name: Number
# description: Echo one bounded numeric quantity.
# domain: example
# interpreter: python3
# mutating: false
# idempotent: true
# arguments:
#   - name: seconds
#     type: number
#     minimum: 0.125
#     maximum: 1200.5
# ---
""",
        encoding="utf-8",
    )
    return load_tool_registry([_source("local", tmp_path)]).get("example.number")


@pytest.mark.parametrize("value", [750, 750.0, 750.125, 0.125, 1200.5])
def test_num001_fractional_quantity_reaches_argv_and_registry_detail(tmp_path, value):
    script = _number_script(tmp_path)
    assert shlex.split(build_script_command(script, {"seconds": value})) == [
        "python3", "-", "--seconds", str(value)
    ]
    assert script.detail()["arguments"] == [{
        "name": "seconds", "type": "number", "required": True,
        "minimum": 0.125, "maximum": 1200.5,
    }]


@pytest.mark.parametrize("value", [
    True, False, "750.125", None, [], {},
    float("nan"), float("inf"), float("-inf"), 0, 1200.5001,
])
def test_num002_rejects_invalid_or_out_of_range_values(tmp_path, value):
    script = _number_script(tmp_path)
    with pytest.raises(ValueError):
        validate_script_arguments(script, {"seconds": value})


@pytest.mark.parametrize("bounds", [
    {}, {"minimum": 0}, {"maximum": 1},
    {"minimum": None, "maximum": 1},
    {"minimum": 1, "maximum": 0},
    {"minimum": "0", "maximum": 1},
    {"minimum": 0, "maximum": "1"},
    {"minimum": False, "maximum": 1},
    {"minimum": 0, "maximum": True},
    {"minimum": float("nan"), "maximum": 1},
    {"minimum": 0, "maximum": float("nan")},
    {"minimum": float("-inf"), "maximum": 1},
    {"minimum": 0, "maximum": float("inf")},
])
def test_num002_rejects_unbounded_or_non_numeric_metadata(bounds):
    with pytest.raises(ValueError):
        ArgumentSpec(name="seconds", type="number", **bounds)


@pytest.mark.parametrize("bounds", [
    {"minimum": 0, "maximum": 1},
    {"minimum": -1.25, "maximum": 2.5},
    {"minimum": 0.125, "maximum": 0.125},
])
def test_num002_accepts_finite_inclusive_bounds(bounds):
    spec = ArgumentSpec(name="seconds", type="number", **bounds)
    assert spec.minimum == bounds["minimum"]
    assert spec.maximum == bounds["maximum"]


@pytest.mark.parametrize("value", [True, False, 2.0, 2.125, "2"])
def test_num003_integer_argument_stays_strict(tmp_path, value):
    _write_script(tmp_path / "echo.sh")
    script = load_tool_registry([_source("local", tmp_path)]).get("system.echo")
    with pytest.raises(ValueError, match="must be an integer"):
        validate_script_arguments(script, {"message": "ok", "count": value})


def test_num003_integer_metadata_retains_existing_coercion_and_rejection():
    spec = ArgumentSpec(name="count", type="integer", minimum="1", maximum=5.0)
    assert type(spec.minimum) is int and spec.minimum == 1
    assert type(spec.maximum) is int and spec.maximum == 5
    with pytest.raises(ValueError):
        ArgumentSpec(name="count", type="integer", minimum=0.125)


def test_num003_number_optional_missing_and_unknown_arguments(tmp_path):
    script = _number_script(tmp_path)
    with pytest.raises(ValueError, match="missing arguments"):
        validate_script_arguments(script, {})
    with pytest.raises(ValueError, match="unknown arguments"):
        validate_script_arguments(script, {"seconds": 1, "extra": 1})
    script.metadata.arguments[0].required = False
    assert validate_script_arguments(script, {}) == []

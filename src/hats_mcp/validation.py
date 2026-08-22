from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import HATSConfig, load_config
from .managed_tools import load_tool_registry, resolve_tool_source_root
from .skills import inspect_skill_source
from .tooling_registry import ToolingRegistry

_SSH_EXECUTABLE = Path("/usr/bin/ssh")


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    text: str
    error_count: int


def _compact_error(exc: Exception) -> str:
    return " ".join(str(exc).split())


def _configured_path(path: str | Path | None) -> Path:
    configured = path if path is not None else os.environ.get("HATS_CONFIG")
    if configured is None or not str(configured).strip():
        raise RuntimeError("HATS_CONFIG is not set")
    return Path(configured).expanduser().resolve(strict=False)


def _probe_write(directory: Path) -> bool:
    fd: int | None = None
    probe: Path | None = None
    try:
        fd, probe_text = tempfile.mkstemp(prefix=".hats-validate-", dir=directory)
        probe = Path(probe_text)
        os.close(fd)
        fd = None
        probe.unlink()
        probe = None
        return True
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)
        if probe is not None:
            try:
                probe.unlink()
            except OSError:
                pass


def _workspace_state(path: Path) -> tuple[bool, str]:
    if path.exists():
        if not path.is_dir():
            return False, "not a directory"
        writable = _probe_write(path)
        return writable, "writable" if writable else "not writable"

    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir():
        return False, "missing"
    if _probe_write(parent):
        return True, f"creatable; parent writable: {parent}"
    return False, f"missing; parent not writable: {parent}"


def _readable_file(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "missing"
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return False, "present, not readable"
    else:
        os.close(fd)
        return True, "present, readable"


def _target_lines(config: HATSConfig) -> tuple[list[str], int]:
    enabled = [(target_id, target) for target_id, target in sorted(config.targets.items()) if target.enabled]
    lines = ["Targets", "  Status: valid", f"  Enabled: {len(enabled)}"]
    for target_id, target in enabled:
        lines.extend(
            [
                "",
                f"  - {target_id} — {target.display_name}",
                f"    Transport: {target.transport}",
                f"    Host: {target.ssh.host}",
                f"    User: {target.ssh.user}",
                f"    Capabilities: {', '.join(target.capabilities) if target.capabilities else 'none'}",
            ]
        )
    return lines, 0


def _tool_source_lines(config: HATSConfig) -> tuple[list[str], int]:
    enabled = [source for source in config.sources.tools if source.enabled]
    details: list[tuple[object, int | None, str | None]] = []
    total_scripts = 0
    errors = 0

    for source in enabled:
        try:
            scripts = load_tool_registry([source]).list()
            count = len(scripts)
            total_scripts += count
            details.append((source, count, None))
        except (ValueError, RuntimeError) as exc:
            errors += 1
            details.append((source, None, _compact_error(exc)))

    cross_source_error: str | None = None
    if enabled and errors == 0:
        try:
            load_tool_registry(enabled)
        except (ValueError, RuntimeError) as exc:
            errors += 1
            cross_source_error = _compact_error(exc)

    lines = [
        "Tool sources",
        f"  Status: {'valid' if errors == 0 else 'invalid'}",
        f"  Sources: {len(enabled)} enabled",
        f"  Scripts: {total_scripts}",
    ]
    for source, count, error in details:
        lines.extend(
            [
                "",
                f"  - {source.id}",
                f"    Type: {source.type}",
                f"    Path: {resolve_tool_source_root(source)}",
            ]
        )
        if error is None:
            lines.append(f"    Scripts: {count}")
        else:
            lines.append(f"    Error: {error}")
    if cross_source_error is not None:
        lines.extend(["", f"  Cross-source error: {cross_source_error}"])
    return lines, errors


def _skill_source_lines(config: HATSConfig) -> tuple[list[str], int]:
    enabled = [source for source in config.sources.skills if source.enabled]
    details: list[tuple[object, dict[str, object] | None, str | None]] = []
    total_skills = 0
    errors = 0

    for source in enabled:
        try:
            _packages, report = inspect_skill_source(source)
            total_skills += int(report["physical_count"])
            details.append((source, report, None))
        except (ValueError, RuntimeError, OSError) as exc:
            errors += 1
            details.append((source, None, _compact_error(exc)))

    lines = [
        "Skill sources",
        f"  Status: {'valid' if errors == 0 else 'invalid'}",
        f"  Sources: {len(enabled)} enabled",
        f"  Skills discovered: {total_skills}",
    ]
    for source, report, error in details:
        lines.extend(
            [
                "",
                f"  - {source.id}",
                f"    Type: {source.type}",
                f"    Path: {source.path}",
            ]
        )
        if error is not None:
            lines.append(f"    Error: {error}")
            continue
        assert report is not None
        lines.append(f"    Skills: {report['physical_count']}")
        if source.type == "filesystem":
            lines.append(f"    Effective locally: {report['effective_count']}")
        else:
            assert source.state is not None
            target = config.targets[source.state.target]
            lines.extend(
                [
                    f"    State target: {source.state.target} — {target.display_name} ({target.ssh.host})",
                    "    Effective state: not checked (network disabled)",
                ]
            )
    return lines, errors


def _tooling_registry_lines(config: HATSConfig) -> tuple[list[str], int]:
    source = config.sources.tooling_registry
    if source is None or not source.enabled:
        return ["Tooling registry", "  Status: not configured"], 0
    try:
        entries = ToolingRegistry(source.path).candidates()
    except ValueError as exc:
        return [
            "Tooling registry",
            "  Status: invalid",
            f"  Path: {source.path}",
            f"  Error: {_compact_error(exc)}",
        ], 1
    return [
        "Tooling registry",
        "  Status: valid",
        f"  Path: {source.path}",
        f"  Promotion candidates: {len(entries)}",
    ], 0


def _workspace_lines(config: HATSConfig) -> tuple[list[str], int]:
    checks: list[tuple[str, str, bool, str]] = []
    errors = 0
    all_writable = True
    names = ["tmp", "runs", "tasks", "trash"]
    if config.workspace.candidates is not None:
        names.append("candidates")
    for name in names:
        value = getattr(config.workspace, name)
        assert value is not None
        ok, state = _workspace_state(Path(value))
        checks.append((name, value, ok, state))
        if not ok:
            errors += 1
        if state != "writable":
            all_writable = False

    if errors:
        status = "invalid"
    elif all_writable:
        status = "writable"
    else:
        status = "ready"
    lines = ["Workspace", f"  Status: {status}"]
    for name, value, ok, state in checks:
        lines.append(f"  - {name}: {value} [{state}]")
    return lines, errors


def _ssh_lines(config: HATSConfig) -> tuple[list[str], int]:
    errors = 0
    executable_ok = _SSH_EXECUTABLE.is_file() and os.access(_SSH_EXECUTABLE, os.X_OK)
    if not executable_ok:
        errors += 1

    enabled = [(target_id, target) for target_id, target in sorted(config.targets.items()) if target.enabled]
    checks: list[tuple[str, object, tuple[bool, str], tuple[bool, str]]] = []
    for target_id, target in enabled:
        identity = _readable_file(Path(target.ssh.identity_file))
        known_hosts = _readable_file(Path(target.ssh.known_hosts_file))
        errors += int(not identity[0]) + int(not known_hosts[0])
        checks.append((target_id, target, identity, known_hosts))

    lines = [
        "SSH credentials",
        f"  Status: {'present' if errors == 0 else 'invalid'}",
        f"  SSH executable: {_SSH_EXECUTABLE} [{'present, executable' if executable_ok else 'missing or not executable'}]",
    ]
    for target_id, target, identity, known_hosts in checks:
        lines.extend(
            [
                "",
                f"  - {target_id} — {target.display_name}",
                f"    Identity: {target.ssh.identity_file} [{identity[1]}]",
                f"    Known hosts: {target.ssh.known_hosts_file} [{known_hosts[1]}]",
            ]
        )
    return lines, errors


def validate_configuration(path: str | Path | None = None) -> ValidationReport:
    try:
        config_path = _configured_path(path)
    except RuntimeError as exc:
        text = "\n".join(
            [
                "HATS configuration validation",
                "",
                "Configuration",
                "  Status: invalid",
                f"  Error: {_compact_error(exc)}",
                "",
                "Result: invalid",
                "1 error",
            ]
        )
        return ValidationReport(valid=False, text=text, error_count=1)

    try:
        config = load_config(config_path)
    except RuntimeError as exc:
        text = "\n".join(
            [
                "HATS configuration validation",
                "",
                "Configuration",
                "  Status: invalid",
                f"  File: {config_path}",
                f"  Error: {_compact_error(exc)}",
                "",
                "Result: invalid",
                "1 error",
            ]
        )
        return ValidationReport(valid=False, text=text, error_count=1)

    sections: list[list[str]] = [
        [
            "HATS configuration validation",
            "",
            "Configuration",
            "  Status: valid",
            f"  File: {config_path}",
        ]
    ]
    error_count = 0
    for builder in (
        _workspace_lines,
        _target_lines,
        _tool_source_lines,
        _skill_source_lines,
        _tooling_registry_lines,
        _ssh_lines,
    ):
        lines, errors = builder(config)
        error_count += errors
        sections.append(lines)

    result = "valid" if error_count == 0 else "invalid"
    trailer = [f"Result: {result}"]
    if error_count:
        trailer.append(f"{error_count} error{'s' if error_count != 1 else ''}")
    sections.append(trailer)
    text = "\n\n".join("\n".join(section) for section in sections)
    return ValidationReport(valid=error_count == 0, text=text, error_count=error_count)

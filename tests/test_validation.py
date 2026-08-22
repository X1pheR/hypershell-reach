from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hats_mcp.cli import main
from hats_mcp.validation import validate_configuration


def _write_skill(root: Path, name: str = "example-skill") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: Example skill for validation.
---

# Example
""",
        encoding="utf-8",
    )


def _write_tool(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "status.py").write_text(
        """# ---
# id: host.status
# name: Host status
# description: Read host status.
# domain: host
# interpreter: python3
# requires: []
# mutating: false
# idempotent: true
# ---
print("ok")
""",
        encoding="utf-8",
    )


def _write_config(tmp_path: Path, *, hermes: bool = False, missing_identity: bool = False) -> Path:
    workspace = tmp_path / "workspace"
    paths = {name: workspace / name for name in ("tmp", "runs", "tasks", "trash")}
    for path in paths.values():
        path.mkdir(parents=True)

    tool_root = tmp_path / "tools"
    skill_root = tmp_path / "skills"
    _write_tool(tool_root)
    _write_skill(skill_root)

    identity = tmp_path / "id_ed25519"
    known_hosts = tmp_path / "known_hosts"
    if not missing_identity:
        identity.write_text("test-key-placeholder\n", encoding="utf-8")
    known_hosts.write_text("example.invalid ssh-ed25519 placeholder\n", encoding="utf-8")

    sources: dict[str, list[dict[str, object]]] = {
        "tools": [{"id": "local", "type": "filesystem", "path": str(tool_root)}],
        "skills": [{"id": "local", "type": "filesystem", "path": str(skill_root)}],
    }
    targets: dict[str, dict[str, object]] = {
        "docker-vm": {
            "display_name": "Docker VM",
            "capabilities": ["linux", "python3"],
            "ssh": {
                "host": "192.0.2.10",
                "user": "operator",
                "identity_file": str(identity),
                "known_hosts_file": str(known_hosts),
            },
        }
    }

    if hermes:
        sources["skills"] = [
            {
                "id": "hermes",
                "type": "hermes",
                "path": str(skill_root),
                "state": {
                    "target": "hermes-agent",
                    "python_executable": "/usr/bin/python3",
                    "config_path": "/home/operator/.hermes/config.yaml",
                    "repo_path": "/opt/hermes-agent",
                    "consumer_platform": "cli",
                },
            }
        ]
        targets["hermes-agent"] = {
            "display_name": "Hermes Agent",
            "capabilities": ["linux", "python3", "hermes"],
            "ssh": {
                "host": "192.0.2.20",
                "user": "operator",
                "identity_file": str(identity),
                "known_hosts_file": str(known_hosts),
            },
        }

    payload = {
        "schema_version": 1,
        "workspace": {name: str(path) for name, path in paths.items()},
        "sources": sources,
        "targets": targets,
    }
    config_path = tmp_path / "hats.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return config_path


def test_validate_reports_operator_visible_configuration(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    report = validate_configuration(config_path)

    assert report.valid is True
    assert report.error_count == 0
    assert "Result: valid" in report.text
    assert "docker-vm — Docker VM" in report.text
    assert "Host: 192.0.2.10" in report.text
    assert f"Path: {tmp_path / 'tools'}" in report.text
    assert f"Path: {tmp_path / 'skills'}" in report.text
    assert f"Identity: {tmp_path / 'id_ed25519'} [present, readable]" in report.text
    assert "Scripts: 1" in report.text
    assert "Skills discovered: 1" in report.text


def test_validate_reports_bundled_tool_source(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["sources"]["tools"] = [{"id": "hats", "type": "bundled"}]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    report = validate_configuration(config_path)

    assert report.valid is True
    assert "- hats" in report.text
    assert "Type: bundled" in report.text
    assert "bundled_tools" in report.text
    assert "Scripts: 5" in report.text


def test_validate_hermes_source_does_not_require_network_state(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, hermes=True)

    report = validate_configuration(config_path)

    assert report.valid is True
    assert "hermes" in report.text
    assert "State target: hermes-agent — Hermes Agent (192.0.2.20)" in report.text
    assert "Effective state: not checked (network disabled)" in report.text
    assert "Skills: 1" in report.text


def test_validate_fails_when_credential_path_is_missing(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, missing_identity=True)

    report = validate_configuration(config_path)

    assert report.valid is False
    assert report.error_count == 1
    assert "Identity:" in report.text
    assert "[missing]" in report.text
    assert "Result: invalid" in report.text


def test_validate_cli_uses_exit_zero_for_valid_configuration(tmp_path: Path, capsys) -> None:
    config_path = _write_config(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        main(["validate", "--config", str(config_path)])

    assert excinfo.value.code == 0
    assert "Result: valid" in capsys.readouterr().out


def test_validate_cli_uses_exit_one_for_invalid_configuration(tmp_path: Path, capsys) -> None:
    config_path = _write_config(tmp_path, missing_identity=True)

    with pytest.raises(SystemExit) as excinfo:
        main(["validate", "--config", str(config_path)])

    assert excinfo.value.code == 1
    assert "Result: invalid" in capsys.readouterr().out

def test_validate_cli_uses_exit_two_for_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["validate", "--unknown-option"])

    assert excinfo.value.code == 2


def test_validate_cli_uses_hats_config_environment(tmp_path: Path, monkeypatch, capsys) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("HATS_CONFIG", str(config_path))

    with pytest.raises(SystemExit) as excinfo:
        main(["validate"])

    assert excinfo.value.code == 0
    assert f"File: {config_path}" in capsys.readouterr().out


def test_validate_reports_tooling_registry_candidates(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    registry_path = tmp_path / "tooling-registry.md"
    registry_path.write_text(
        """### ATR-007 — Repeated selector ambiguity
- **Status:** guarded
- **Promotion:** candidate
- **Promotion reason:** Repeated and deterministic.
- **Helper candidate or implementation:** Shared selector linting.
""",
        encoding="utf-8",
    )
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["sources"]["tooling_registry"] = {
        "type": "markdown",
        "path": str(registry_path),
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    report = validate_configuration(config_path)

    assert report.valid is True
    assert "Tooling registry" in report.text
    assert "Status: valid" in report.text
    assert "Promotion candidates: 1" in report.text


def test_validate_rejects_invalid_tooling_registry(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    registry_path = tmp_path / "tooling-registry.md"
    registry_path.write_text(
        """### ATR-007 — Unknown promotion state
- **Status:** guarded
- **Promotion:** maybe
- **Promotion reason:** Unknown.
- **Helper candidate or implementation:** Unknown.
""",
        encoding="utf-8",
    )
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["sources"]["tooling_registry"] = {
        "type": "markdown",
        "path": str(registry_path),
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    report = validate_configuration(config_path)

    assert report.valid is False
    assert "Tooling registry" in report.text
    assert "Status: invalid" in report.text
    assert "unsupported promotion state" in report.text



def test_validate_checks_configured_candidate_workspace(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    candidate_root = tmp_path / "workspace" / "candidates"
    candidate_root.mkdir()
    payload["workspace"]["candidates"] = str(candidate_root)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    report = validate_configuration(config_path)

    assert report.valid is True
    assert f"candidates: {candidate_root} [writable]" in report.text

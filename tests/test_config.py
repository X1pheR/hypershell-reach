from __future__ import annotations

import pytest
from pydantic import ValidationError

from hats_mcp.config import HATSConfig, load_config


def _config() -> dict:
    return {
        "schema_version": 1,
        "workspace": {
            "tmp": "/tmp/hats",
            "runs": "/var/lib/hats/runs",
            "tasks": "/var/lib/hats/tasks",
            "trash": "/var/lib/hats/trash",
        },
        "targets": {
            "docker": {
                "display_name": "Docker host",
                "capabilities": ["docker", "linux", "docker"],
                "ssh": {
                    "host": "192.0.2.10",
                    "user": "operator",
                    "identity_file": "/run/key",
                    "known_hosts_file": "/run/known_hosts",
                },
            }
        },
    }


def test_config_normalizes_target_capabilities() -> None:
    config = HATSConfig.model_validate(_config())
    assert config.targets["docker"].capabilities == ["docker", "linux"]


def test_enabled_target_requires_host() -> None:
    payload = _config()
    payload["targets"]["docker"]["ssh"].pop("host")
    with pytest.raises(ValidationError, match="enabled targets require"):
        HATSConfig.model_validate(payload)


def test_relative_workspace_path_is_rejected() -> None:
    payload = _config()
    payload["workspace"]["tmp"] = "relative/tmp"
    with pytest.raises(ValidationError, match="workspace paths must be absolute"):
        HATSConfig.model_validate(payload)


def test_relative_ssh_path_is_rejected() -> None:
    payload = _config()
    payload["targets"]["docker"]["ssh"]["identity_file"] = "relative/key"
    with pytest.raises(ValidationError, match="SSH file paths must be absolute"):
        HATSConfig.model_validate(payload)


def test_missing_environment_path_is_rejected(monkeypatch) -> None:
    monkeypatch.delenv("HATS_CONFIG", raising=False)
    with pytest.raises(RuntimeError, match="HATS_CONFIG is not set"):
        load_config()


def test_yaml_config_loads(tmp_path) -> None:
    path = tmp_path / "hats.yaml"
    path.write_text(
        """schema_version: 1
workspace:
  tmp: /tmp/hats
  runs: /var/lib/hats/runs
  tasks: /var/lib/hats/tasks
  trash: /var/lib/hats/trash
targets:
  docker:
    display_name: Docker host
    ssh:
      host: 192.0.2.10
      user: operator
      identity_file: /run/key
      known_hosts_file: /run/known_hosts
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.targets["docker"].ssh.host == "192.0.2.10"


def test_tool_source_path_must_be_absolute() -> None:
    payload = _config()
    payload["sources"] = {"tools": [{"id": "local", "path": "relative/tools"}]}
    with pytest.raises(ValidationError, match="tool source paths must be absolute"):
        HATSConfig.model_validate(payload)


def test_bundled_tool_source_requires_no_path() -> None:
    payload = _config()
    payload["sources"] = {"tools": [{"id": "hats", "type": "bundled"}]}
    config = HATSConfig.model_validate(payload)
    assert config.sources.tools[0].path is None


def test_bundled_tool_source_rejects_path() -> None:
    payload = _config()
    payload["sources"] = {"tools": [{"id": "hats", "type": "bundled", "path": "/tools"}]}
    with pytest.raises(ValidationError, match="must not configure path"):
        HATSConfig.model_validate(payload)


def test_filesystem_tool_source_requires_path() -> None:
    payload = _config()
    payload["sources"] = {"tools": [{"id": "local", "type": "filesystem"}]}
    with pytest.raises(ValidationError, match="require path"):
        HATSConfig.model_validate(payload)


def test_tool_source_ids_must_be_unique() -> None:
    payload = _config()
    payload["sources"] = {
        "tools": [
            {"id": "local", "path": "/sources/one"},
            {"id": "local", "path": "/sources/two"},
        ]
    }
    with pytest.raises(ValidationError, match="duplicate tool source IDs"):
        HATSConfig.model_validate(payload)


def test_hermes_skill_source_requires_state_projection() -> None:
    payload = _config()
    payload["sources"] = {
        "skills": [{"id": "hermes", "type": "hermes", "path": "/skills"}]
    }
    with pytest.raises(ValidationError, match="require a state projection"):
        HATSConfig.model_validate(payload)


def test_hermes_skill_state_target_must_exist() -> None:
    payload = _config()
    payload["sources"] = {
        "skills": [
            {
                "id": "hermes",
                "type": "hermes",
                "path": "/skills",
                "state": {
                    "target": "missing",
                    "python_executable": "/usr/bin/python3",
                    "config_path": "/home/user/.hermes/config.yaml",
                    "repo_path": "/opt/hermes-agent",
                },
            }
        ]
    }
    with pytest.raises(ValidationError, match="unknown Hermes skill-state targets"):
        HATSConfig.model_validate(payload)


def test_tooling_registry_path_must_be_absolute() -> None:
    payload = _config()
    payload["sources"] = {
        "tooling_registry": {"type": "markdown", "path": "relative/registry.md"}
    }
    with pytest.raises(ValidationError, match="tooling registry path must be absolute"):
        HATSConfig.model_validate(payload)


def test_tooling_registry_source_is_optional() -> None:
    config = HATSConfig.model_validate(_config())
    assert config.sources.tooling_registry is None


def test_candidate_workspace_path_is_optional_and_must_be_absolute() -> None:
    config = HATSConfig.model_validate(_config())
    assert config.workspace.candidates is None

    payload = _config()
    payload["workspace"]["candidates"] = "/var/lib/hats/candidates"
    config = HATSConfig.model_validate(payload)
    assert config.workspace.candidates == "/var/lib/hats/candidates"

    payload["workspace"]["candidates"] = "relative/candidates"
    with pytest.raises(ValidationError, match="workspace paths must be absolute"):
        HATSConfig.model_validate(payload)

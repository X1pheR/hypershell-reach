from __future__ import annotations

import pytest
from pydantic import ValidationError

from hats_mcp.config import HATSConfig, load_config


def _config() -> dict:
    return {
        "schema_version": 1,
        "workspace": {
            "tmp": "/tmp/hats",
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

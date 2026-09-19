from __future__ import annotations

import pytest
from pydantic import ValidationError

from hypershell_reach.config import ReachConfig, Target, load_config


def _config() -> dict:
    return {
        "schema_version": 1,
        "workspace": {
            "tmp": "/tmp/reach",
            "runs": "/var/lib/reach/runs",
            "tasks": "/var/lib/reach/tasks",
            "trash": "/var/lib/reach/trash",
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


def test_target_optional_heavy_concurrency_limit_is_typed() -> None:
    target = Target.model_validate(
        {
            "display_name": "Laptop",
            "capabilities": ["windows"],
            "max_heavy_concurrency": 1,
            "ssh": {
                "host": "203.0.113.10",
                "user": "operator",
                "identity_file": "/run/key",
                "known_hosts_file": "/run/known_hosts",
            },
        }
    )
    assert target.max_heavy_concurrency == 1

    unlimited = Target.model_validate(
        {
            "display_name": "Other",
            "capabilities": ["linux"],
            "ssh": {
                "host": "203.0.113.11",
                "user": "operator",
                "identity_file": "/run/key",
                "known_hosts_file": "/run/known_hosts",
            },
        }
    )
    assert unlimited.max_heavy_concurrency is None


def test_config_normalizes_target_capabilities() -> None:
    config = ReachConfig.model_validate(_config())
    assert config.targets["docker"].capabilities == ["docker", "linux"]


def test_enabled_target_requires_host() -> None:
    payload = _config()
    payload["targets"]["docker"]["ssh"].pop("host")
    with pytest.raises(ValidationError, match="enabled targets require"):
        ReachConfig.model_validate(payload)


def test_relative_workspace_path_is_rejected() -> None:
    payload = _config()
    payload["workspace"]["tmp"] = "relative/tmp"
    with pytest.raises(ValidationError, match="workspace paths must be absolute"):
        ReachConfig.model_validate(payload)


def test_relative_ssh_path_is_rejected() -> None:
    payload = _config()
    payload["targets"]["docker"]["ssh"]["identity_file"] = "relative/key"
    with pytest.raises(ValidationError, match="SSH file paths must be absolute"):
        ReachConfig.model_validate(payload)


def test_missing_environment_path_is_rejected(monkeypatch) -> None:
    monkeypatch.delenv("REACH_CONFIG", raising=False)
    with pytest.raises(RuntimeError, match="REACH_CONFIG is not set"):
        load_config()


def test_yaml_config_loads(tmp_path) -> None:
    path = tmp_path / "reach.yaml"
    path.write_text(
        """schema_version: 1
workspace:
  tmp: /tmp/reach
  runs: /var/lib/reach/runs
  tasks: /var/lib/reach/tasks
  trash: /var/lib/reach/trash
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
        ReachConfig.model_validate(payload)


def test_bundled_tool_source_requires_no_path() -> None:
    payload = _config()
    payload["sources"] = {"tools": [{"id": "reach", "type": "bundled"}]}
    config = ReachConfig.model_validate(payload)
    assert config.sources.tools[0].path is None


def test_bundled_tool_source_rejects_path() -> None:
    payload = _config()
    payload["sources"] = {"tools": [{"id": "reach", "type": "bundled", "path": "/tools"}]}
    with pytest.raises(ValidationError, match="must not configure path"):
        ReachConfig.model_validate(payload)


def test_filesystem_tool_source_requires_path() -> None:
    payload = _config()
    payload["sources"] = {"tools": [{"id": "local", "type": "filesystem"}]}
    with pytest.raises(ValidationError, match="require path"):
        ReachConfig.model_validate(payload)


def test_tool_source_ids_must_be_unique() -> None:
    payload = _config()
    payload["sources"] = {
        "tools": [
            {"id": "local", "path": "/sources/one"},
            {"id": "local", "path": "/sources/two"},
        ]
    }
    with pytest.raises(ValidationError, match="duplicate tool source IDs"):
        ReachConfig.model_validate(payload)


def test_hermes_skill_source_requires_state_projection() -> None:
    payload = _config()
    payload["sources"] = {
        "skills": [{"id": "hermes", "type": "hermes", "path": "/skills"}]
    }
    with pytest.raises(ValidationError, match="require a state projection"):
        ReachConfig.model_validate(payload)


def test_hermes_skill_source_accepts_snapshot_state() -> None:
    payload = _config()
    payload["sources"] = {
        "skills": [
            {
                "id": "hermes",
                "type": "hermes",
                "path": "/skills",
                "state": {
                    "mode": "snapshot",
                    "snapshot_path": "/state/hermes.json",
                },
            }
        ]
    }

    config = ReachConfig.model_validate(payload)

    assert config.sources.skills[0].state is not None
    assert config.sources.skills[0].state.mode == "snapshot"
    assert config.sources.skills[0].state.snapshot_path == "/state/hermes.json"
    assert config.sources.skills[0].state.target is None


def test_hermes_snapshot_state_rejects_remote_projection_fields() -> None:
    payload = _config()
    payload["sources"] = {
        "skills": [
            {
                "id": "hermes",
                "type": "hermes",
                "path": "/skills",
                "state": {
                    "mode": "snapshot",
                    "snapshot_path": "/state/hermes.json",
                    "target": "docker",
                },
            }
        ]
    }

    with pytest.raises(ValidationError, match="snapshot Hermes skill state must not configure remote projection"):
        ReachConfig.model_validate(payload)


def test_hermes_skill_source_accepts_absolute_additional_paths() -> None:
    payload = _config()
    payload["sources"] = {
        "skills": [
            {
                "id": "hermes",
                "type": "hermes",
                "path": "/skills",
                "additional_paths": ["/project-skills", "/project-skills", "/external-skills"],
                "state": {
                    "target": "docker",
                    "python_executable": "/usr/bin/python3",
                    "config_path": "/home/user/.hermes/config.yaml",
                    "repo_path": "/opt/hermes-agent",
                },
            }
        ]
    }

    config = ReachConfig.model_validate(payload)

    assert config.sources.skills[0].additional_paths == ["/project-skills", "/external-skills"]


def test_additional_skill_source_paths_must_be_absolute_and_hermes_only() -> None:
    payload = _config()
    payload["sources"] = {
        "skills": [
            {
                "id": "hermes",
                "type": "hermes",
                "path": "/skills",
                "additional_paths": ["relative/project-skills"],
                "state": {
                    "target": "docker",
                    "python_executable": "/usr/bin/python3",
                    "config_path": "/home/user/.hermes/config.yaml",
                    "repo_path": "/opt/hermes-agent",
                },
            }
        ]
    }
    with pytest.raises(ValidationError, match="additional skill source paths must be absolute"):
        ReachConfig.model_validate(payload)

    payload = _config()
    payload["sources"] = {
        "skills": [
            {
                "id": "local",
                "type": "filesystem",
                "path": "/skills",
                "additional_paths": ["/extra"],
            }
        ]
    }
    with pytest.raises(ValidationError, match="supported only for Hermes"):
        ReachConfig.model_validate(payload)


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
        ReachConfig.model_validate(payload)


def test_tooling_registry_path_must_be_absolute() -> None:
    payload = _config()
    payload["sources"] = {
        "tooling_registry": {"type": "markdown", "path": "relative/registry.md"}
    }
    with pytest.raises(ValidationError, match="tooling registry path must be absolute"):
        ReachConfig.model_validate(payload)


def test_tooling_registry_source_is_optional() -> None:
    config = ReachConfig.model_validate(_config())
    assert config.sources.tooling_registry is None


def test_candidate_workspace_path_is_optional_and_must_be_absolute() -> None:
    config = ReachConfig.model_validate(_config())
    assert config.workspace.candidates is None

    payload = _config()
    payload["workspace"]["candidates"] = "/var/lib/reach/candidates"
    config = ReachConfig.model_validate(payload)
    assert config.workspace.candidates == "/var/lib/reach/candidates"

    payload["workspace"]["candidates"] = "relative/candidates"
    with pytest.raises(ValidationError, match="workspace paths must be absolute"):
        ReachConfig.model_validate(payload)


def test_topology_is_optional_and_uses_stable_node_ids() -> None:
    config = ReachConfig.model_validate(_config())
    assert config.topology is None

    payload = _config()
    payload["topology"] = {"node_id": "home", "primary_node_id": "home"}
    config = ReachConfig.model_validate(payload)
    assert config.topology is not None
    assert config.topology.node_id == "home"
    assert config.topology.primary_node_id == "home"

    payload["topology"]["node_id"] = "Home Reach"
    with pytest.raises(ValidationError, match="invalid topology node ID"):
        ReachConfig.model_validate(payload)


def test_synchronous_timeout_can_be_stricter_than_execution_timeout() -> None:
    payload = _config()
    payload["defaults"] = {
        "max_timeout_seconds": 300,
        "max_synchronous_timeout_seconds": 90,
    }
    config = ReachConfig.model_validate(payload)
    target = config.targets["docker"]

    assert config.resolved_max_timeout(target) == 300
    assert config.resolved_max_synchronous_timeout(target) == 90


def test_synchronous_timeout_cannot_exceed_execution_timeout() -> None:
    payload = _config()
    payload["defaults"] = {
        "max_timeout_seconds": 120,
        "max_synchronous_timeout_seconds": 121,
    }
    with pytest.raises(ValidationError, match="synchronous timeout"):
        ReachConfig.model_validate(payload)


def test_executor_socket_is_optional_and_private_local_path() -> None:
    config = ReachConfig.model_validate(_config())
    assert config.executor.socket_path is None
    assert config.executor.max_concurrency == 2

    payload = _config()
    payload["executor"] = {
        "socket_path": "/var/lib/reach/executor.sock",
        "max_concurrency": 3,
    }
    config = ReachConfig.model_validate(payload)
    assert config.executor.socket_path == "/var/lib/reach/executor.sock"
    assert config.executor.max_concurrency == 3


def test_executor_socket_must_be_absolute() -> None:
    payload = _config()
    payload["executor"] = {"socket_path": "relative/executor.sock"}
    with pytest.raises(ValidationError, match="executor socket path must be absolute"):
        ReachConfig.model_validate(payload)

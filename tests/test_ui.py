from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from hats_mcp.config import HATSConfig
from hats_mcp.runs import RunStore
from hats_mcp.tasks import TaskStore
from hats_mcp.ui import create_app


def _config(tmp_path: Path) -> HATSConfig:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "inspect.py").write_text(
        """# ---\n# id: system.inspect\n# name: Inspect system\n# description: Read safe system metadata.\n# domain: system\n# interpreter: python3\n# requires: [linux]\n# mutating: false\n# idempotent: true\n# ---\nprint('ok')\n""",
        encoding="utf-8",
    )
    skills = tmp_path / "skills" / "example"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example skill.\n---\n# Example\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.md"
    registry.write_text(
        """### ATR-999 — Example gap\n- **Status:** observed\n- **Promotion:** candidate\n- **Promotion reason:** Repeated bounded gap.\n- **Helper candidate or implementation:** example.inspect\n""",
        encoding="utf-8",
    )
    return HATSConfig.model_validate(
        {
            "schema_version": 1,
            "workspace": {
                "tmp": str(tmp_path / "tmp"),
                "runs": str(tmp_path / "runs"),
                "tasks": str(tmp_path / "tasks"),
                "trash": str(tmp_path / "trash"),
            },
            "sources": {
                "tools": [{"id": "local", "path": str(tmp_path / "tools")}],
                "skills": [{"id": "local", "path": str(tmp_path / "skills")}],
                "tooling_registry": {"path": str(registry)},
            },
            "targets": {
                "docker": {
                    "display_name": "Docker host",
                    "capabilities": ["linux", "docker"],
                    "ssh": {
                        "host": "192.0.2.55",
                        "user": "operator",
                        "identity_file": "/run/secrets/id_ed25519",
                        "known_hosts_file": "/run/secrets/known_hosts",
                    },
                }
            },
        }
    )


def _client(tmp_path: Path) -> TestClient:
    config = _config(tmp_path)
    run_store = RunStore(config.workspace.runs)
    run = run_store.create(
        operation="run_command",
        target="docker",
        timeout_seconds=30,
        may_mutate=False,
    )
    run_store.finish(
        run.id,
        {
            "status": "succeeded",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 2,
            "stdout": {"bytes": 0, "truncated": False},
            "stderr": {"bytes": 0, "truncated": False},
        },
    )
    TaskStore(config.workspace.tasks, config.workspace.trash).create(
        title="Example task",
        objective="Private objective must not be rendered in the list view.",
    )
    return TestClient(create_app(config))


def test_ui_exposes_six_read_only_views_and_health(tmp_path) -> None:
    client = _client(tmp_path)

    home = client.get("/")
    assert home.status_code == 200
    for label in ("Targets", "Managed tooling", "Runs", "Tasks", "Skills", "Tooling Candidates"):
        assert label in home.text
    assert client.get("/healthz").json() == {"status": "ok", "role": "hats-ui"}
    assert client.post("/targets").status_code == 405


def test_targets_view_does_not_render_connection_or_credential_fields(tmp_path) -> None:
    client = _client(tmp_path)
    response = client.get("/targets")

    assert response.status_code == 200
    assert "Docker host" in response.text
    assert "docker" in response.text
    assert "192.0.2.55" not in response.text
    assert "operator" not in response.text
    assert "/run/secrets/" not in response.text


def test_views_render_existing_domain_summaries_without_private_task_content(tmp_path) -> None:
    client = _client(tmp_path)

    assert "system.inspect" in client.get("/tooling").text
    assert "run_command" in client.get("/runs").text
    tasks = client.get("/tasks").text
    assert "Example task" in tasks
    assert "Private objective" not in tasks
    assert "Example skill." in client.get("/skills").text
    candidates = client.get("/candidates").text
    assert "ATR-999" in candidates
    assert "Repeated bounded gap." in candidates


def test_html_responses_send_defensive_headers(tmp_path) -> None:
    response = _client(tmp_path).get("/")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in response.headers["content-security-policy"]

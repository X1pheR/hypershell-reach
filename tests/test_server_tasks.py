from __future__ import annotations

import json

import pytest

from hats_mcp import server
from hats_mcp.config import HATSConfig
from hats_mcp.runs import RunStore
from hats_mcp.tasks import TaskStore


def _config(tmp_path) -> HATSConfig:
    return HATSConfig.model_validate(
        {
            "schema_version": 1,
            "workspace": {
                "tmp": str(tmp_path / "tmp"),
                "runs": str(tmp_path / "runs"),
                "tasks": str(tmp_path / "tasks"),
                "trash": str(tmp_path / "trash"),
            },
            "targets": {
                "example": {
                    "display_name": "Example",
                    "capabilities": ["linux", "bash"],
                    "ssh": {
                        "host": "203.0.113.10",
                        "user": "operator",
                        "identity_file": "/run/key",
                        "known_hosts_file": "/run/known_hosts",
                    },
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_task_lifecycle_and_run_linkage(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs))
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )

    created_content = await server.call_tool(
        "create_task",
        {"title": "Example", "objective": "Keep continuity", "next_action": "Inspect"},
    )
    created = json.loads(created_content[0].text)

    async def fake_run_ssh(**kwargs):
        return {
            "target": kwargs["target_id"],
            "status": "succeeded",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 1,
            "stdout": {"text": "ok", "bytes": 2, "truncated": False},
            "stderr": {"text": "", "bytes": 0, "truncated": False},
        }

    monkeypatch.setattr(server, "run_ssh", fake_run_ssh)
    run_content = await server.call_tool(
        "run_command",
        {"target": "example", "command": "true", "task_id": created["id"]},
    )
    run = json.loads(run_content[0].text)

    assert server._run_store_instance.get(run["run_id"]).task_id == created["id"]

    await server.call_tool("update_task", {"task_id": created["id"], "status": "completed"})
    archived_content = await server.call_tool("archive_task", {"task_id": created["id"]})
    archived = json.loads(archived_content[0].text)
    assert archived["archived_at"] is not None

    rejected = await server.call_tool(
        "run_command",
        {"target": "example", "command": "true", "task_id": created["id"]},
    )
    assert rejected[0].text == f"ERROR: task is archived: {created['id']}"
    assert len(server._run_store_instance.list()) == 1


@pytest.mark.asyncio
async def test_unknown_task_rejects_execution_before_run_creation(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs))
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )

    rejected = await server.call_tool(
        "run_command",
        {"target": "example", "command": "true", "task_id": "task-20260812T120000000000Z-123456789abc"},
    )

    assert rejected[0].text.startswith("ERROR: unknown task:")
    assert server._run_store_instance.list() == []

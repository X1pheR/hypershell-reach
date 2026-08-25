from __future__ import annotations

import json

import pytest

from hats_mcp import server
from hats_mcp.config import HATSConfig
from hats_mcp.runs import RunStore


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
async def test_run_command_persists_metadata_without_command(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs))

    async def fake_run_ssh(**kwargs):
        assert kwargs["remote_command"] == "echo sensitive-command"
        return {
            "target": kwargs["target_id"],
            "status": "succeeded",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 1,
            "stdout": {"text": "sensitive-output", "bytes": 16, "truncated": False},
            "stderr": {"text": "", "bytes": 0, "truncated": False},
        }

    monkeypatch.setattr(server, "run_ssh", fake_run_ssh)
    content = await server.call_tool(
        "run_command",
        {"target": "example", "command": "echo sensitive-command", "purpose": "Verify safe command execution persistence."},
    )
    result = json.loads(content[0].text)
    record = server._run_store_instance.get(result["run_id"])
    raw = (tmp_path / "runs" / f"{record.id}.json").read_text(encoding="utf-8")

    assert result["execution"]["status"] == "succeeded"
    assert record.operation == "run_command"
    assert record.status == "succeeded"
    assert "sensitive-command" not in raw
    assert "sensitive-output" not in raw


@pytest.mark.asyncio
async def test_local_execution_error_finishes_run(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs))

    async def fake_run_ssh(**kwargs):
        raise RuntimeError("configured key unavailable")

    monkeypatch.setattr(server, "run_ssh", fake_run_ssh)
    content = await server.call_tool(
        "run_command",
        {"target": "example", "command": "true", "purpose": "Exercise local execution failure handling."},
    )

    assert content[0].text.startswith("ERROR:")
    records = server._run_store_instance.list()
    assert len(records) == 1
    assert records[0].status == "local_error"
    assert records[0].ambiguous is False
    assert records[0].error_type == "RuntimeError"


@pytest.mark.asyncio
async def test_start_command_accepts_duration_above_synchronous_ceiling(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    config.defaults.max_timeout_seconds = 300
    config.defaults.max_synchronous_timeout_seconds = 90
    config.executor.socket_path = str(tmp_path / "executor.sock")
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs, reconcile_modes={"sync"}))
    captured = {}

    async def fake_submit_execution(current_config, submission):
        captured["config"] = current_config
        captured["submission"] = submission
        return {"run_id": "run-async-example", "status": "running", "execution_mode": "async"}

    monkeypatch.setattr(server, "submit_execution", fake_submit_execution, raising=False)
    content = await server.call_tool(
        "start_command",
        {
            "target": "example",
            "command": "sleep 180",
            "timeout_seconds": 180,
            "purpose": "Prove durable asynchronous command submission.",
        },
    )
    result = json.loads(content[0].text)

    assert result == {
        "run_id": "run-async-example",
        "status": "running",
        "execution_mode": "async",
    }
    assert captured["submission"].remote_command == "sleep 180"
    assert captured["submission"].timeout_seconds == 180


@pytest.mark.asyncio
async def test_run_command_rejects_duration_above_synchronous_ceiling(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    config.defaults.max_timeout_seconds = 300
    config.defaults.max_synchronous_timeout_seconds = 90
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs, reconcile_modes={"sync"}))

    content = await server.call_tool(
        "run_command",
        {
            "target": "example",
            "command": "sleep 91",
            "timeout_seconds": 91,
            "purpose": "Prove synchronous transport guardrail.",
        },
    )

    assert content[0].text.startswith("ERROR:")
    assert "synchronous" in content[0].text.lower()
    assert server._run_store_instance.list() == []


@pytest.mark.asyncio
async def test_start_shell_submits_body_without_persisting_it_in_mcp_run_store(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    config.defaults.max_timeout_seconds = 300
    config.defaults.max_synchronous_timeout_seconds = 90
    config.executor.socket_path = str(tmp_path / "executor.sock")
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs, reconcile_modes={"sync"}))
    captured = {}

    async def fake_submit_execution(current_config, submission):
        captured["submission"] = submission
        return {"run_id": "run-async-shell", "status": "running", "execution_mode": "async"}

    monkeypatch.setattr(server, "submit_execution", fake_submit_execution)
    content = await server.call_tool(
        "start_shell",
        {
            "target": "example",
            "interpreter": "bash",
            "script": "sleep 180\nprintf SECRET-SHELL-BODY",
            "timeout_seconds": 180,
            "purpose": "Prove durable asynchronous shell submission.",
        },
    )
    result = json.loads(content[0].text)

    assert result["run_id"] == "run-async-shell"
    assert captured["submission"].remote_command == "bash -s --"
    assert captured["submission"].stdin_text == "sleep 180\nprintf SECRET-SHELL-BODY"
    assert server._run_store_instance.list() == []


@pytest.mark.asyncio
async def test_cancel_run_requires_explicit_confirmation(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    config.executor.socket_path = str(tmp_path / "executor.sock")
    monkeypatch.setattr(server, "_config", config, raising=False)
    called = False

    async def fake_cancel_execution(current_config, run_id):
        nonlocal called
        called = True
        return {"run_id": run_id, "status": "interrupted", "cancelled": True}

    monkeypatch.setattr(server, "cancel_execution", fake_cancel_execution)
    content = await server.call_tool(
        "cancel_run",
        {"run_id": "run-20260825T000000000000Z-aaaaaaaaaaaa", "confirm": False},
    )

    assert content[0].text.startswith("ERROR:")
    assert "confirm=true" in content[0].text
    assert called is False


@pytest.mark.asyncio
async def test_cancel_run_delegates_to_async_executor(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    config.executor.socket_path = str(tmp_path / "executor.sock")
    monkeypatch.setattr(server, "_config", config, raising=False)
    captured = {}

    async def fake_cancel_execution(current_config, run_id):
        captured["config"] = current_config
        captured["run_id"] = run_id
        return {"run_id": run_id, "status": "interrupted", "cancelled": True}

    monkeypatch.setattr(server, "cancel_execution", fake_cancel_execution)
    run_id = "run-20260825T000000000000Z-aaaaaaaaaaaa"
    content = await server.call_tool("cancel_run", {"run_id": run_id, "confirm": True})
    result = json.loads(content[0].text)

    assert captured == {"config": config, "run_id": run_id}
    assert result == {"run_id": run_id, "status": "interrupted", "cancelled": True}


@pytest.mark.asyncio
async def test_stdio_startup_reconciles_only_synchronous_runs(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "load_config", lambda: config)

    class ExpectedStop(Exception):
        pass

    def capture_run_store(root, **kwargs):
        assert root == config.workspace.runs
        assert kwargs["reconcile_modes"] == {"sync"}
        raise ExpectedStop

    monkeypatch.setattr(server, "RunStore", capture_run_store)

    with pytest.raises(ExpectedStop):
        await server.run_stdio()

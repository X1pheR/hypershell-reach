from __future__ import annotations

from run_storage_helpers import run_payload, replace_run_payload

import json
from pathlib import Path

import pytest

from hypershell_reach.config import ReachConfig
from hypershell_reach import server


def _write_script(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
# ---
# id: system.echo
# name: Echo
# description: Echo one message.
# domain: system
# interpreter: bash
# requires: [linux]
# mutating: false
# idempotent: true
# timeout_seconds: 15
# arguments:
#   - name: message
#     type: string
#     required: true
# ---
printf '%s\\n' \"$@\"
""",
        encoding="utf-8",
    )


def _config(tool_root: Path) -> ReachConfig:
    return ReachConfig.model_validate(
        {
            "schema_version": 1,
            "workspace": {
                "tmp": str(tool_root / "tmp"),
                "runs": str(tool_root / "runs"),
                "tasks": str(tool_root / "tasks"),
                "trash": str(tool_root / "trash"),
            },
            "sources": {"tools": [{"id": "local", "path": str(tool_root)}]},
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
async def test_readonly_managed_route_has_truthful_mcp_annotations_and_rejects_mutations(
    tmp_path, monkeypatch
) -> None:
    script_path = tmp_path / "echo.sh"
    _write_script(script_path)
    monkeypatch.setattr(server, "_config", _config(tmp_path), raising=False)
    monkeypatch.setattr(server, "_run_store_instance", None)
    recorded = []

    async def fake_run_ssh(**kwargs):
        recorded.append(kwargs)
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
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name in ("run_readonly_script", "start_readonly_script"):
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.destructiveHint is False
        assert tools[name].annotations.idempotentHint is True
    assert tools["run_script"].annotations.destructiveHint is True

    payload = json.loads((await server.call_tool(
        "run_readonly_script",
        {"script_id": "system.echo", "target": "example",
         "purpose": "Inspect one bounded managed script using a read-only MCP route.",
         "arguments": {"message": "safe"}},
    ))[0].text)
    assert payload["execution"]["status"] == "succeeded"
    assert payload["mutating"] is False
    assert payload["idempotent"] is True
    assert server._run_store_instance.get(payload["run_id"]).may_mutate is False

    script_path.write_text(script_path.read_text().replace("# mutating: false", "# mutating: true"))
    denied = (await server.call_tool(
        "run_readonly_script",
        {"script_id": "system.echo", "target": "example",
         "purpose": "Reject mutating registry scripts before a remote execution."},
    ))[0].text
    assert "read-only" in denied.lower()
    assert len(recorded) == 1


@pytest.mark.asyncio
async def test_readonly_async_route_enforces_contract_and_allows_long_tool_timeout(
    tmp_path, monkeypatch
) -> None:
    script_path = tmp_path / "echo.sh"
    _write_script(script_path)
    source = script_path.read_text().replace("# timeout_seconds: 15", "# timeout_seconds: 120")
    script_path.write_text(source)
    config = _config(tmp_path)
    config.defaults.max_synchronous_timeout_seconds = 10
    config.executor.socket_path = str(tmp_path / "executor.sock")
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", None)
    captured = []

    async def fake_submit_execution(current_config, submission):
        captured.append(submission)
        return {"run_id": "run-readonly", "status": "running", "execution_mode": "async"}

    monkeypatch.setattr(server, "submit_execution", fake_submit_execution)
    response = json.loads((await server.call_tool(
        "start_readonly_script",
        {"script_id": "system.echo", "target": "example",
         "purpose": "Start a verified read-only script within a registered timeout.",
         "arguments": {"message": "safe"}},
    ))[0].text)
    assert response["run_id"] == "run-readonly"
    assert captured[0].may_mutate is False
    assert captured[0].idempotent is True
    assert captured[0].timeout_seconds == 120

    script_path.write_text(source.replace("# idempotent: true", "# idempotent: false"))
    denied = (await server.call_tool(
        "start_readonly_script",
        {"script_id": "system.echo", "target": "example",
         "purpose": "Reject a tool whose idempotence contract is not verified."},
    ))[0].text
    assert "read-only" in denied.lower()
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_run_script_uses_registry_content_and_typed_arguments(tmp_path, monkeypatch) -> None:
    script_path = tmp_path / "echo.sh"
    _write_script(script_path)
    monkeypatch.setattr(server, "_config", _config(tmp_path), raising=False)
    monkeypatch.setattr(server, "_run_store_instance", None)
    captured = {}

    async def fake_run_ssh(**kwargs):
        captured.update(kwargs)
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

    content = await server.call_tool(
        "run_script",
        {"script_id": "system.echo", "target": "example", "purpose": "Validate managed script execution metadata.", "arguments": {"message": "hello world"}},
    )
    result = json.loads(content[0].text)

    assert result["script_id"] == "system.echo"
    assert result["source"] == "local"
    assert result["execution"]["status"] == "succeeded"
    assert captured["remote_command"] == "bash -s -- --message 'hello world'"
    assert captured["stdin_text"] == script_path.read_text(encoding="utf-8")
    assert captured["timeout_seconds"] == 15
    run = server._run_store_instance.get(result["run_id"])
    raw = run_payload(tmp_path / "runs", run.id)
    assert run.may_mutate is False
    assert run.idempotent is True
    assert run.purpose == "Validate managed script execution metadata."
    assert run.argument_names == ["message"]
    assert "hello world" not in raw
    assert script_path.read_text(encoding="utf-8") not in raw


@pytest.mark.asyncio
async def test_start_script_preserves_managed_tool_contract_and_accepts_long_timeout(tmp_path, monkeypatch) -> None:
    script_path = tmp_path / "echo.sh"
    _write_script(script_path)
    config = _config(tmp_path)
    config.defaults.max_synchronous_timeout_seconds = 10
    config.executor.socket_path = str(tmp_path / "executor.sock")
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", None)
    captured = {}

    async def fake_submit_execution(current_config, submission):
        captured["config"] = current_config
        captured["submission"] = submission
        return {"run_id": "run-async-script", "status": "running", "execution_mode": "async"}

    monkeypatch.setattr(server, "submit_execution", fake_submit_execution)
    content = await server.call_tool(
        "start_script",
        {
            "script_id": "system.echo",
            "target": "example",
            "purpose": "Validate durable managed-script execution metadata.",
            "arguments": {"message": "hello world"},
        },
    )
    result = json.loads(content[0].text)

    assert result == {"run_id": "run-async-script", "status": "running", "execution_mode": "async"}
    submission = captured["submission"]
    assert submission.operation == "run_script"
    assert submission.remote_command == "bash -s -- --message 'hello world'"
    assert submission.stdin_text == script_path.read_text(encoding="utf-8")
    assert submission.timeout_seconds == 15
    assert submission.may_mutate is False
    assert submission.idempotent is True
    assert submission.script_id == "system.echo"
    assert submission.script_source == "local"
    assert submission.argument_names == ["message"]
    assert list((tmp_path / "runs").glob("run-*.json")) == []


@pytest.mark.asyncio
async def test_run_script_rejects_managed_timeout_above_synchronous_ceiling(tmp_path, monkeypatch) -> None:
    script_path = tmp_path / "echo.sh"
    _write_script(script_path)
    config = _config(tmp_path)
    config.defaults.max_synchronous_timeout_seconds = 10
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", None)

    content = await server.call_tool(
        "run_script",
        {
            "script_id": "system.echo",
            "target": "example",
            "purpose": "Validate the synchronous managed-script transport guardrail.",
            "arguments": {"message": "hello world"},
        },
    )

    assert content[0].text.startswith("ERROR:")
    assert "synchronous" in content[0].text.lower()
    assert "start_script" in content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["run_script", "start_script"])
@pytest.mark.parametrize("value,valid", [
    (750, True), (750.0, True), (750.125, True),
    ("750.125", False), (True, False), (float("nan"), False),
    (float("inf"), False), (-0.001, False), (1200.001, False),
])
async def test_num004_numeric_validation_precedes_dispatch(
    tmp_path, monkeypatch, operation, value, valid
):
    path = tmp_path / "echo.sh"
    _write_script(path)
    text = path.read_text().replace(
        "#     type: string", "#     type: number\n#     minimum: 0\n#     maximum: 1200"
    )
    path.write_text(text)
    config = _config(tmp_path)
    config.executor.socket_path = str(tmp_path / "executor.sock")
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", None)
    captured = []

    async def fake_run_ssh(**kwargs):
        captured.append(kwargs["remote_command"])
        return {
            "target": "example", "status": "succeeded", "exit_code": 0,
            "timed_out": False, "duration_ms": 1,
            "stdout": {"text": "", "bytes": 0, "truncated": False},
            "stderr": {"text": "", "bytes": 0, "truncated": False},
        }

    async def fake_submit(current_config, submission):
        captured.append(submission.remote_command)
        return {"run_id": "run-numeric", "status": "running", "execution_mode": "async"}

    monkeypatch.setattr(server, "run_ssh", fake_run_ssh)
    monkeypatch.setattr(server, "submit_execution", fake_submit)
    content = await server.call_tool(operation, {
        "script_id": "system.echo", "target": "example",
        "purpose": "Verify bounded numeric execution.",
        "arguments": {"message": value},
    })
    if valid:
        assert not content[0].text.startswith("ERROR:")
        assert captured == [f"bash -s -- --message {value}"]
    else:
        assert content[0].text.startswith("ERROR:")
        assert captured == []

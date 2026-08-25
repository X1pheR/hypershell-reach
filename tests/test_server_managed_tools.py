from __future__ import annotations

import json
from pathlib import Path

import pytest

from hats_mcp.config import HATSConfig
from hats_mcp import server


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


def _config(tool_root: Path) -> HATSConfig:
    return HATSConfig.model_validate(
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
    raw = (tmp_path / "runs" / f"{run.id}.json").read_text(encoding="utf-8")
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

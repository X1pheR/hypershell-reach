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

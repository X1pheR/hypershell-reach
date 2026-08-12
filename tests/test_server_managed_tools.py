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
        {"script_id": "system.echo", "target": "example", "arguments": {"message": "hello world"}},
    )
    result = json.loads(content[0].text)

    assert result["script_id"] == "system.echo"
    assert result["source"] == "local"
    assert result["execution"]["status"] == "succeeded"
    assert captured["remote_command"] == "bash -s -- --message 'hello world'"
    assert captured["stdin_text"] == script_path.read_text(encoding="utf-8")
    assert captured["timeout_seconds"] == 15

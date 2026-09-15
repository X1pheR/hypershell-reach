from __future__ import annotations

from starlette.testclient import TestClient

from hypershell_reach import service
from hypershell_reach.config import ReachConfig
from hypershell_reach.service import create_service_app


def _config(tmp_path) -> ReachConfig:
    tool = tmp_path / "tools" / "system" / "inspect.sh"
    tool.parent.mkdir(parents=True)
    tool.write_text(
        """#!/bin/sh
# id: system.inspect
# name: Inspect
# description: Inspect the test target.
# domain: system
# mutating: false
# idempotent: true
# requires: [linux, sh]
echo ok
""",
        encoding="utf-8",
    )
    return ReachConfig.model_validate(
        {
            "schema_version": 1,
            "workspace": {
                "tmp": str(tmp_path / "tmp"),
                "runs": str(tmp_path / "runs"),
                "tasks": str(tmp_path / "tasks"),
                "trash": str(tmp_path / "trash"),
            },
            "sources": {
                "tools": [{"id": "local", "type": "filesystem", "path": str(tmp_path / "tools")}],
                "skills": [],
            },
            "targets": {
                "example": {
                    "display_name": "Example",
                    "capabilities": ["linux", "sh"],
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


def test_service_hosts_ui_api_and_streamable_http_mcp(tmp_path) -> None:
    app = create_service_app(_config(tmp_path))
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200

        summary = client.get("/api/v1/summary")
        assert summary.status_code == 200
        assert summary.json()["product"] == "Hypershell Reach"

        initialize = client.post(
            "/mcp",
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "reach-test", "version": "1"},
                },
            },
        )
        assert initialize.status_code == 200
        payload = initialize.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
        assert payload["result"]["serverInfo"]["name"] == "hypershell-reach"


def test_cli_exports_hermes_snapshot_without_starting_service(tmp_path, monkeypatch, capsys) -> None:
    config = _config(tmp_path)
    expected = {
        "schema_version": 1,
        "source_id": "hermes",
        "content_fingerprint": "a" * 64,
        "snapshot_revision": "b" * 64,
    }

    async def fake_export(received_config, source_id):
        assert received_config is config
        assert source_id == "hermes"
        return expected

    monkeypatch.setattr(service, "load_config", lambda _path=None: config)
    monkeypatch.setattr(service, "export_hermes_snapshot", fake_export, raising=False)
    monkeypatch.setattr(service.uvicorn, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("service started")))

    service.main(["export-hermes-snapshot", "--config", "/ignored/reach.yaml", "--source", "hermes"])

    import json

    assert json.loads(capsys.readouterr().out) == expected

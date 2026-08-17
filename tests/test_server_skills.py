from __future__ import annotations

import json

import pytest

from hats_mcp import server
from hats_mcp.config import HATSConfig


def _config(tmp_path) -> HATSConfig:
    skill_dir = tmp_path / "skills/example"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example skill.\n---\n# Example\n\nInstructions.\n",
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
                "skills": [
                    {
                        "id": "hermes",
                        "type": "hermes",
                        "path": str(tmp_path / "skills"),
                        "os_platform": "linux",
                        "state": {
                            "target": "hermes",
                            "python_executable": "/usr/bin/python3",
                            "config_path": "/tmp/config.yaml",
                            "repo_path": "/tmp/hermes",
                            "consumer_platform": "cli",
                        },
                    }
                ]
            },
            "targets": {
                "hermes": {
                    "display_name": "Hermes",
                    "capabilities": ["linux", "python3"],
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
async def test_catalog_and_get_use_live_hermes_state(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    calls = 0

    async def fake_run_ssh(**kwargs):
        nonlocal calls
        calls += 1
        return {
            "status": "succeeded",
            "stdout": {
                "text": json.dumps(
                    {
                        "schema_version": 1,
                        "consumer_platform": "cli",
                        "disabled": [],
                        "external_dirs": [],
                        "effective_names": ["example"],
                    }
                ),
                "bytes": 120,
                "truncated": False,
            },
            "stderr": {"text": "", "bytes": 0, "truncated": False},
        }

    monkeypatch.setattr(server, "run_ssh", fake_run_ssh)
    catalog_content = await server.call_tool("skills_catalog", {})
    catalog = json.loads(catalog_content[0].text)
    detail_content = await server.call_tool("skill_get", {"skill_id": "hermes:example"})
    detail = json.loads(detail_content[0].text)

    assert catalog["skills"] == [
        {
            "id": "hermes:example",
            "name": "example",
            "description": "Example skill.",
            "category": None,
        }
    ]
    assert catalog["categories"] == []
    assert catalog["count"] == 1
    assert len(catalog["catalog_revision"]) == 64
    assert "Reuse this catalog" in catalog["hint"]
    assert detail["skill_md"]["content"].startswith("---")
    assert calls == 1

    refreshed_content = await server.call_tool("skill_get", {"skill_id": "hermes:example", "refresh": True})
    refreshed = json.loads(refreshed_content[0].text)
    assert refreshed["name"] == "example"
    assert calls == 2


@pytest.mark.asyncio
async def test_disabled_skill_cannot_be_loaded(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)

    async def fake_run_ssh(**kwargs):
        return {
            "status": "succeeded",
            "stdout": {
                "text": json.dumps(
                    {
                        "schema_version": 1,
                        "consumer_platform": "cli",
                        "disabled": ["example"],
                        "external_dirs": [],
                        "effective_names": [],
                    }
                ),
                "bytes": 100,
                "truncated": False,
            },
            "stderr": {"text": "", "bytes": 0, "truncated": False},
        }

    monkeypatch.setattr(server, "run_ssh", fake_run_ssh)
    content = await server.call_tool("skill_get", {"skill_id": "hermes:example"})

    assert content[0].text == "ERROR: skill is not active: hermes:example"


@pytest.mark.asyncio
async def test_skill_get_default_reads_full_supported_skill(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    skill_path = tmp_path / "skills/example/SKILL.md"
    skill_path.write_text(
        "---\nname: example\ndescription: Example skill.\n---\n# Example\n\n" + ("x" * 40_000) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "_config", config, raising=False)

    async def fake_run_ssh(**kwargs):
        return {
            "status": "succeeded",
            "stdout": {
                "text": json.dumps(
                    {
                        "schema_version": 1,
                        "consumer_platform": "cli",
                        "disabled": [],
                        "external_dirs": [],
                        "effective_names": ["example"],
                    }
                ),
                "bytes": 120,
                "truncated": False,
            },
            "stderr": {"text": "", "bytes": 0, "truncated": False},
        }

    monkeypatch.setattr(server, "run_ssh", fake_run_ssh)
    content = await server.call_tool("skill_get", {"skill_id": "hermes:example"})
    detail = json.loads(content[0].text)

    assert detail["skill_md"]["truncated"] is False
    assert detail["skill_md"]["total_bytes"] > 32_768

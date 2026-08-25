from __future__ import annotations

import json

import pytest

from hypershell_reach import server
from hypershell_reach.config import ReachConfig


def _config(tmp_path, registry_path=None) -> ReachConfig:
    sources = {}
    if registry_path is not None:
        sources["tooling_registry"] = {"type": "markdown", "path": str(registry_path)}
    return ReachConfig.model_validate(
        {
            "schema_version": 1,
            "workspace": {
                "tmp": str(tmp_path / "tmp"),
                "runs": str(tmp_path / "runs"),
                "tasks": str(tmp_path / "tasks"),
                "trash": str(tmp_path / "trash"),
            },
            "sources": sources,
            "targets": {
                "example": {
                    "display_name": "Example",
                    "capabilities": ["linux"],
                    "ssh": {
                        "host": "example.invalid",
                        "user": "operator",
                        "identity_file": "/run/key",
                        "known_hosts_file": "/run/known_hosts",
                    },
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_tooling_candidates_reports_unconfigured_registry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(server, "_config", _config(tmp_path), raising=False)
    content = await server.call_tool("tooling_candidates", {})
    assert json.loads(content[0].text) == {
        "configured": False,
        "candidates": [],
        "count": 0,
    }


@pytest.mark.asyncio
async def test_tooling_candidates_returns_explicit_reviewed_candidates(tmp_path, monkeypatch) -> None:
    registry_path = tmp_path / "registry.md"
    registry_path.write_text(
        """### ATR-007 — Repeated selector ambiguity
- **Status:** guarded
- **Promotion:** candidate
- **Promotion reason:** Repeated and deterministic.
- **Helper candidate or implementation:** Shared selector linting.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "_config", _config(tmp_path, registry_path), raising=False)
    content = await server.call_tool("tooling_candidates", {})
    result = json.loads(content[0].text)
    assert result["configured"] is True
    assert result["count"] == 1
    assert result["candidates"][0]["id"] == "ATR-007"
    assert result["candidates"][0]["promotion"] == "candidate"


@pytest.mark.asyncio
async def test_preview_candidate_imports_maps_legacy_feed_without_promoting(tmp_path, monkeypatch) -> None:
    registry_path = tmp_path / "registry.md"
    registry_path.write_text(
        """### ATR-007 — Repeated selector ambiguity
- **Status:** guarded
- **Promotion:** candidate
- **Promotion reason:** Repeated and deterministic.
- **Helper candidate or implementation:** Shared selector linting.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "_config", _config(tmp_path, registry_path), raising=False)

    content = await server.call_tool("preview_candidate_imports", {})
    result = json.loads(content[0].text)

    assert result["configured"] is True
    assert result["source_schema"] == "tooling-registry-v1"
    assert result["target_schema"] == "candidate-v1"
    assert result["count"] == 1
    assert result["drafts"][0]["id"] == "ATR-007"
    assert result["drafts"][0]["proposed_tool_id"] is None
    assert "ownership.owner_id" in result["drafts"][0]["missing_required_fields"]

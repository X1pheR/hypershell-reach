from __future__ import annotations

import json

import pytest

from hats_mcp import server
from hats_mcp.candidates import CandidateStore
from hats_mcp.config import HATSConfig
from hats_mcp.tasks import TaskStore


def _config(tmp_path, *, candidates: bool = True) -> HATSConfig:
    workspace = {
        "tmp": str(tmp_path / "tmp"),
        "runs": str(tmp_path / "runs"),
        "tasks": str(tmp_path / "tasks"),
        "trash": str(tmp_path / "trash"),
    }
    if candidates:
        workspace["candidates"] = str(tmp_path / "candidates")
    return HATSConfig.model_validate(
        {
            "schema_version": 1,
            "workspace": workspace,
            "sources": {"tools": [{"id": "hats", "type": "bundled"}]},
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


def _create_input() -> dict:
    return {
        "candidate_id": "ATR-022",
        "title": "OIDC integration migration preflight",
        "problem": {
            "summary": "Repeated OIDC migrations require the same preflight checks.",
            "cause": "The checks are reconstructed ad hoc for each migration.",
            "recurrence": "The same sequence is needed across multiple integrations.",
            "evidence": ["Repeated operator workflow with deterministic preconditions."],
        },
        "proposal": {
            "capability": "Validate OIDC migration preconditions without changing provider state.",
            "proposed_tool_id": "oidc.migration-preflight",
            "required_inputs": [
                {"name": "provider_id", "description": "Stable provider identifier."}
            ],
            "expected_outputs": [
                {"name": "preflight", "description": "Deterministic validation result."}
            ],
            "safety": {
                "mutating": False,
                "secret_access": False,
                "boundary": "Read-only provider metadata and configuration checks only.",
            },
            "acceptance": ["Returns deterministic pass/fail for every declared precondition."],
        },
        "ownership": {"owner_id": "X1pheR/homelab-agent-tooling-skills-mcp"},
        "promotion_rationale": "The recurring bounded workflow is reusable and deterministic.",
    }


def _install_stores(tmp_path, monkeypatch, config: HATSConfig) -> None:
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(
        server,
        "_candidate_store_instance",
        CandidateStore(config.workspace.candidates) if config.workspace.candidates else None,
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )


@pytest.mark.asyncio
async def test_candidate_mcp_lifecycle_is_typed_and_approval_is_dedicated(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    _install_stores(tmp_path, monkeypatch, config)

    created = json.loads((await server.call_tool("create_candidate", _create_input()))[0].text)
    assert created["promotion"]["state"] == "candidate"
    assert created["revision"] == 1

    ambiguous = await server.call_tool(
        "update_candidate",
        {"candidate_id": "ATR-022", "expected_revision": 1, "state": "approved"},
    )
    assert ambiguous[0].text.startswith("ERROR: invalid tool input:")
    assert "Extra inputs are not permitted" in ambiguous[0].text

    approved = json.loads(
        (
            await server.call_tool(
                "approve_candidate",
                {
                    "candidate_id": "ATR-022",
                    "expected_revision": 1,
                    "approval_rationale": "Operator explicitly authorized implementation.",
                },
            )
        )[0].text
    )
    assert approved["promotion"]["state"] == "approved"
    assert approved["revision"] == 2

    task = json.loads(
        (
            await server.call_tool(
                "create_task", {"title": "Implement candidate", "objective": "Build the approved tool."}
            )
        )[0].text
    )
    linked = json.loads(
        (
            await server.call_tool(
                "link_candidate_task",
                {"candidate_id": "ATR-022", "expected_revision": 2, "task_id": task["id"]},
            )
        )[0].text
    )
    assert linked["implementation"]["task_id"] == task["id"]
    assert linked["revision"] == 3

    completed = json.loads(
        (
            await server.call_tool(
                "complete_candidate",
                {
                    "candidate_id": "ATR-022",
                    "expected_revision": 3,
                    "outcome": "automated",
                    "completion_rationale": "Bundled managed tool is accepted.",
                    "final_reference": {"kind": "managed-tool", "id": "filesystem.compare-modes"},
                },
            )
        )[0].text
    )
    assert completed["promotion"]["state"] == "automated"
    assert completed["implementation"]["final_reference"] == {
        "kind": "managed-tool",
        "id": "filesystem.compare-modes",
    }


@pytest.mark.asyncio
async def test_candidate_final_managed_tool_reference_must_exist(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    _install_stores(tmp_path, monkeypatch, config)
    await server.call_tool("create_candidate", _create_input())
    await server.call_tool(
        "approve_candidate",
        {
            "candidate_id": "ATR-022",
            "expected_revision": 1,
            "approval_rationale": "Operator authorized implementation.",
        },
    )

    rejected = await server.call_tool(
        "complete_candidate",
        {
            "candidate_id": "ATR-022",
            "expected_revision": 2,
            "outcome": "implemented",
            "completion_rationale": "Implementation claims completion.",
            "final_reference": {"kind": "managed-tool", "id": "missing.tool"},
        },
    )

    assert rejected[0].text == "ERROR: unknown script: missing.tool"
    assert server._candidate_store_instance.get("ATR-022").promotion.state == "approved"


@pytest.mark.asyncio
async def test_candidate_task_link_requires_existing_hats_task(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    _install_stores(tmp_path, monkeypatch, config)
    await server.call_tool("create_candidate", _create_input())
    await server.call_tool(
        "approve_candidate",
        {
            "candidate_id": "ATR-022",
            "expected_revision": 1,
            "approval_rationale": "Operator authorized implementation.",
        },
    )

    rejected = await server.call_tool(
        "link_candidate_task",
        {
            "candidate_id": "ATR-022",
            "expected_revision": 2,
            "task_id": "task-20260812T120000000000Z-123456789abc",
        },
    )
    assert rejected[0].text.startswith("ERROR: unknown task:")
    assert server._candidate_store_instance.get("ATR-022").implementation.task_id is None


@pytest.mark.asyncio
async def test_candidate_store_is_additive_and_unconfigured_is_readable(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path, candidates=False)
    _install_stores(tmp_path, monkeypatch, config)

    listed = json.loads((await server.call_tool("list_candidates", {}))[0].text)
    assert listed == {"configured": False, "candidates": [], "count": 0}

    rejected = await server.call_tool("create_candidate", _create_input())
    assert rejected[0].text == "ERROR: candidate store is not configured"

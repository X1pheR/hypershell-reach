from __future__ import annotations

import asyncio

from hats_mcp.server import list_tools


def test_tools_have_truthful_annotations() -> None:
    tools = {tool.name: tool for tool in asyncio.run(list_tools())}
    expected = {
        "skills_catalog": (True, False, True, False),
        "skill_get": (True, False, True, False),
        "skill_read_file": (True, False, True, False),
        "tooling_candidates": (True, False, True, False),
        "preview_candidate_imports": (True, False, True, False),
        "list_candidates": (True, False, True, False),
        "get_candidate": (True, False, True, False),
        "create_candidate": (False, False, False, False),
        "update_candidate": (False, False, False, False),
        "approve_candidate": (False, False, True, False),
        "block_candidate": (False, False, True, False),
        "mark_candidate_not_warranted": (False, False, True, False),
        "link_candidate_task": (False, False, True, False),
        "complete_candidate": (False, False, True, False),
        "list_targets": (True, False, True, False),
        "list_scripts": (True, False, True, False),
        "get_script": (True, False, True, False),
        "list_runs": (True, False, True, False),
        "get_run": (True, False, True, False),
        "set_run_retained": (False, False, True, False),
        "list_tasks": (True, False, True, False),
        "get_task": (True, False, True, False),
        "create_task": (False, False, False, False),
        "update_task": (False, False, False, False),
        "archive_task": (False, False, True, False),
        "run_script": (False, True, False, True),
        "run_command": (False, True, False, True),
        "run_shell": (False, True, False, True),
    }

    assert set(tools) == set(expected)
    for name, values in expected.items():
        annotations = tools[name].annotations
        assert annotations is not None
        assert (
            annotations.readOnlyHint,
            annotations.destructiveHint,
            annotations.idempotentHint,
            annotations.openWorldHint,
        ) == values


def test_candidate_approval_tool_does_not_confuse_state_mechanics_with_authorization() -> None:
    tools = {tool.name: tool for tool in asyncio.run(list_tools())}
    description = tools["approve_candidate"].description.lower()
    assert "explicit operator authorization" in description
    assert "does not grant authorization" in description

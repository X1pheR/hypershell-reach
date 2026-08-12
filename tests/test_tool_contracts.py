from __future__ import annotations

import asyncio

from hats_mcp.server import list_tools


def test_tools_have_truthful_annotations() -> None:
    tools = {tool.name: tool for tool in asyncio.run(list_tools())}
    expected = {
        "list_targets": (True, False, True, False),
        "list_scripts": (True, False, True, False),
        "get_script": (True, False, True, False),
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

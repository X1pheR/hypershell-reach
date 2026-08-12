from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import Sequence
from typing import Any, Literal

import mcp.types as types
from mcp.server import Server
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import HATSConfig, load_config
from .execution import run_ssh
from .managed_tools import build_script_command, ensure_target_compatible, load_tool_registry

app = Server("hats")
_config: HATSConfig


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=63)
    command: str = Field(min_length=1, max_length=65_536)
    timeout_seconds: int = Field(default=45, ge=1, le=900)


class ShellInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=63)
    interpreter: Literal["sh", "bash"] = "sh"
    script: str = Field(min_length=1, max_length=262_144)
    timeout_seconds: int = Field(default=90, ge=1, le=900)


class GetScriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=3, max_length=190)


class RunScriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=3, max_length=190)
    target: str = Field(min_length=1, max_length=63)
    arguments: dict[str, Any] = Field(default_factory=dict)


def _target_runtime(target_id: str, requested_timeout: int) -> tuple[Any, int, int]:
    target = _config.enabled_target(target_id)
    max_timeout = _config.resolved_max_timeout(target)
    if requested_timeout > max_timeout:
        raise ValueError(
            f"timeout_seconds exceeds configured maximum for {target_id}: {max_timeout}"
        )
    return (
        target,
        _config.resolved_connect_timeout(target),
        _config.resolved_max_output(target),
    )


def _tool_registry():
    return load_tool_registry(_config.sources.tools)


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_targets",
            description=(
                "List configured HATS target IDs, capabilities and execution limits. "
                "Connection addresses, usernames and credential paths are not returned."
            ),
            inputSchema=EmptyInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="list_scripts",
            description=(
                "List registered managed scripts and compact execution metadata. "
                "Only configured filesystem sources are scanned."
            ),
            inputSchema=EmptyInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="get_script",
            description=(
                "Return metadata for one registered managed script, including typed arguments "
                "and source provenance. Script source code is not returned."
            ),
            inputSchema=GetScriptInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="run_script",
            description=(
                "Run one registered managed script on a compatible configured target. "
                "The caller supplies a script ID and typed argument object, never a filesystem "
                "path, raw argv or arbitrary environment."
            ),
            inputSchema=RunScriptInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
        ),
        types.Tool(
            name="run_command",
            description=(
                "Run one non-interactive command on a configured target. The server enforces "
                "strict host-key checking, key-only SSH, no forwarding, bounded output and "
                "a bounded timeout. Commands are never retried automatically."
            ),
            inputSchema=CommandInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
        ),
        types.Tool(
            name="run_shell",
            description=(
                "Run bounded sh or bash input over SSH stdin on a configured target. Use this "
                "for loops, here-documents or multi-step shell input. Scripts are never retried."
            ),
            inputSchema=ShellInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
        ),
    ]


@app.call_tool()
async def call_tool(
    name: str, arguments: Any
) -> Sequence[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if arguments is None:
        arguments = {}

    try:
        if name == "list_targets":
            EmptyInput(**arguments)
            result = {
                "targets": [
                    {
                        "id": target_id,
                        "display_name": target.display_name,
                        "transport": target.transport,
                        "capabilities": target.capabilities,
                        "enabled": target.enabled,
                        "max_timeout_seconds": _config.resolved_max_timeout(target),
                        "max_output_bytes": _config.resolved_max_output(target),
                    }
                    for target_id, target in sorted(_config.targets.items())
                ]
            }
        elif name == "list_scripts":
            EmptyInput(**arguments)
            result = {"scripts": [script.summary() for script in _tool_registry().list()]}
        elif name == "get_script":
            args = GetScriptInput(**arguments)
            result = _tool_registry().get(args.script_id).detail()
        elif name == "run_script":
            args = RunScriptInput(**arguments)
            script = _tool_registry().get(args.script_id)
            target, connect_timeout, max_output = _target_runtime(
                args.target, script.metadata.timeout_seconds
            )
            ensure_target_compatible(script, target.capabilities)
            execution = await run_ssh(
                target_id=args.target,
                target=target,
                remote_command=build_script_command(script, args.arguments),
                timeout_seconds=script.metadata.timeout_seconds,
                connect_timeout_seconds=connect_timeout,
                max_output_bytes=max_output,
                stdin_text=script.content,
            )
            result = {
                "script_id": script.metadata.id,
                "source": script.source_id,
                "sha256": script.sha256,
                "mutating": script.metadata.mutating,
                "idempotent": script.metadata.idempotent,
                "execution": execution,
            }
        elif name == "run_command":
            args = CommandInput(**arguments)
            target, connect_timeout, max_output = _target_runtime(
                args.target, args.timeout_seconds
            )
            result = await run_ssh(
                target_id=args.target,
                target=target,
                remote_command=args.command,
                timeout_seconds=args.timeout_seconds,
                connect_timeout_seconds=connect_timeout,
                max_output_bytes=max_output,
            )
        elif name == "run_shell":
            args = ShellInput(**arguments)
            target, connect_timeout, max_output = _target_runtime(
                args.target, args.timeout_seconds
            )
            result = await run_ssh(
                target_id=args.target,
                target=target,
                remote_command=f"{args.interpreter} -s --",
                timeout_seconds=args.timeout_seconds,
                connect_timeout_seconds=connect_timeout,
                max_output_bytes=max_output,
                stdin_text=args.script,
            )
        else:
            raise ValueError(f"unknown tool: {name}")
    except ValidationError as exc:
        return [types.TextContent(type="text", text=f"ERROR: invalid tool input: {exc}")]
    except (ValueError, RuntimeError) as exc:
        return [types.TextContent(type="text", text=f"ERROR: {exc}")]
    except Exception:
        await app.request_context.session.send_log_message("error", traceback.format_exc())
        raise

    return [
        types.TextContent(
            type="text",
            text=json.dumps(result, indent=2, ensure_ascii=False),
        )
    ]


async def run_stdio() -> None:
    from mcp.server.stdio import stdio_server

    global _config
    _config = load_config()

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    asyncio.run(run_stdio())

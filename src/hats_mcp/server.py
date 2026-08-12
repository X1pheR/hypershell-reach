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
from .skills import HermesState, build_skill_registry, list_skill_files, read_skill_file
from .runs import RunOperation, RunStatus, RunStore
from .tasks import TaskStatus, TaskStore

app = Server("hats")
_config: HATSConfig
_run_store_instance: RunStore | None = None
_task_store_instance: TaskStore | None = None


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=3, max_length=190)
    max_bytes: int = Field(default=32_768, ge=1, le=131_072)


class SkillReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=3, max_length=190)
    relative_path: str = Field(min_length=1, max_length=4_096)
    offset: int = Field(default=0, ge=0)
    max_bytes: int = Field(default=65_536, ge=1, le=131_072)


class CommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=63)
    command: str = Field(min_length=1, max_length=65_536)
    timeout_seconds: int = Field(default=45, ge=1, le=900)
    task_id: str | None = Field(default=None, min_length=1, max_length=128)


class ShellInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=63)
    interpreter: Literal["sh", "bash"] = "sh"
    script: str = Field(min_length=1, max_length=262_144)
    timeout_seconds: int = Field(default=90, ge=1, le=900)
    task_id: str | None = Field(default=None, min_length=1, max_length=128)


class GetScriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=3, max_length=190)


class RunScriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=3, max_length=190)
    target: str = Field(min_length=1, max_length=63)
    arguments: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = Field(default=None, min_length=1, max_length=128)


class ListRunsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RunStatus | None = None
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=100, ge=1, le=500)


class GetRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=128)


class RetainRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=128)
    retained: bool


class ListTasksInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TaskStatus | None = None
    include_archived: bool = False
    limit: int = Field(default=100, ge=1, le=500)


class GetTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)


class CreateTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=4_000)
    project_ref: str | None = Field(default=None, min_length=1, max_length=256)
    next_action: str | None = Field(default=None, min_length=1, max_length=2_000)
    retained: bool = False


class UpdateTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    objective: str | None = Field(default=None, min_length=1, max_length=4_000)
    project_ref: str | None = Field(default=None, min_length=1, max_length=256)
    clear_project_ref: bool = False
    status: TaskStatus | None = None
    next_action: str | None = Field(default=None, min_length=1, max_length=2_000)
    clear_next_action: bool = False
    retained: bool | None = None


class ArchiveTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)


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


def _run_store() -> RunStore:
    global _run_store_instance
    if _run_store_instance is None:
        _run_store_instance = RunStore(
            _config.workspace.runs,
            completed_days=_config.retention.runs.completed_days,
        )
        _run_store_instance.cleanup()
    return _run_store_instance


def _task_store() -> TaskStore:
    global _task_store_instance
    if _task_store_instance is None:
        _task_store_instance = TaskStore(
            _config.workspace.tasks,
            _config.workspace.trash,
            archived_days=_config.retention.tasks.archived_days,
        )
        _task_store_instance.cleanup()
    return _task_store_instance


async def _tracked_ssh_run(
    *,
    operation: RunOperation,
    target_id: str,
    target: Any,
    remote_command: str,
    timeout_seconds: int,
    connect_timeout_seconds: int,
    max_output_bytes: int,
    may_mutate: bool,
    task_id: str | None = None,
    stdin_text: str | None = None,
    script_id: str | None = None,
    script_source: str | None = None,
    script_sha256: str | None = None,
    argument_names: list[str] | None = None,
) -> tuple[str, dict[str, object]]:
    if task_id is not None:
        _task_store().require_open(task_id)
    store = _run_store()
    record = store.create(
        operation=operation,
        target=target_id,
        task_id=task_id,
        script_id=script_id,
        script_source=script_source,
        script_sha256=script_sha256,
        argument_names=argument_names,
        timeout_seconds=timeout_seconds,
        may_mutate=may_mutate,
    )
    try:
        execution = await run_ssh(
            target_id=target_id,
            target=target,
            remote_command=remote_command,
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            max_output_bytes=max_output_bytes,
            stdin_text=stdin_text,
        )
    except asyncio.CancelledError:
        store.interrupt(record.id)
        raise
    except (ValueError, RuntimeError) as exc:
        store.fail_local(record.id, type(exc).__name__)
        raise
    except Exception as exc:
        store.mark_unknown(record.id, type(exc).__name__)
        raise
    store.finish(record.id, execution)
    return record.id, execution


async def _hermes_state(source) -> HermesState:
    assert source.state is not None
    target = _config.enabled_target(source.state.target)
    projector_path = __import__("pathlib").Path(__file__).with_name("hermes_state_projector.py")
    projector = projector_path.read_text(encoding="utf-8")
    import shlex

    argv = [
        source.state.python_executable,
        "-",
        "--config-path",
        source.state.config_path,
        "--repo-path",
        source.state.repo_path,
    ]
    if source.state.consumer_platform:
        argv.extend(["--consumer-platform", source.state.consumer_platform])
    result = await run_ssh(
        target_id=source.state.target,
        target=target,
        remote_command=" ".join(shlex.quote(value) for value in argv),
        timeout_seconds=source.state.timeout_seconds,
        connect_timeout_seconds=_config.resolved_connect_timeout(target),
        max_output_bytes=min(_config.resolved_max_output(target), 131_072),
        stdin_text=projector,
    )
    if result.get("status") != "succeeded":
        raise RuntimeError(
            f"Hermes skill-state projection failed for {source.id}: {result.get('status')}"
        )
    stdout = result.get("stdout") if isinstance(result.get("stdout"), dict) else {}
    if stdout.get("truncated"):
        raise RuntimeError(f"Hermes skill-state projection was truncated for {source.id}")
    try:
        payload = json.loads(str(stdout.get("text") or ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Hermes skill-state projection returned invalid JSON for {source.id}") from exc
    if payload.get("schema_version") != 1:
        raise RuntimeError(f"unsupported Hermes skill-state projection for {source.id}")
    return HermesState(
        effective_names=frozenset(str(value) for value in payload.get("effective_names", [])),
        disabled=frozenset(str(value) for value in payload.get("disabled", [])),
        external_dirs=tuple(str(value) for value in payload.get("external_dirs", [])),
        consumer_platform=payload.get("consumer_platform"),
        stderr_bytes=(
            result.get("stderr", {}).get("bytes", 0)
            if isinstance(result.get("stderr"), dict)
            else 0
        ),
    )


async def _skill_registry():
    states: dict[str, HermesState] = {}
    for source in _config.sources.skills:
        if source.enabled and source.type == "hermes":
            states[source.id] = await _hermes_state(source)
    return build_skill_registry(_config.sources.skills, states)


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="skills_catalog",
            description=(
                "Return compact metadata for configured Agent Skills. Hermes sources use a "
                "live read-only state projection so the default catalog follows Hermes' "
                "effective skill set."
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
            name="skill_get",
            description=(
                "Return metadata, file manifest and a bounded first chunk of SKILL.md for one "
                "active source-qualified skill ID."
            ),
            inputSchema=SkillGetInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="skill_read_file",
            description=(
                "Read a bounded byte range from one supporting file inside an active skill "
                "package. Binary files return metadata without base64 content."
            ),
            inputSchema=SkillReadFileInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
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
            name="list_runs",
            description=(
                "List persisted HATS execution records. Run records contain bounded metadata, "
                "not command text, scripts, argument values or output content."
            ),
            inputSchema=ListRunsInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="get_run",
            description="Return one persisted HATS execution record without execution content.",
            inputSchema=GetRunInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="set_run_retained",
            description=(
                "Set or clear the retention override for one run record. This changes only local "
                "HATS state and does not execute a remote operation."
            ),
            inputSchema=RetainRunInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="list_tasks",
            description=(
                "List HATS task-continuity records. Tasks are local continuity state, not "
                "projects or execution authorization."
            ),
            inputSchema=ListTasksInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="get_task",
            description="Return one HATS task-continuity record.",
            inputSchema=GetTaskInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="create_task",
            description=(
                "Create durable local continuity state for substantial or interruption-prone "
                "work. This does not execute a remote operation."
            ),
            inputSchema=CreateTaskInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="update_task",
            description=(
                "Update one current HATS task record. Terminal task status cannot be reopened."
            ),
            inputSchema=UpdateTaskInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=False,
            ),
        ),
        types.Tool(
            name="archive_task",
            description=(
                "Move one completed or cancelled HATS task from the active task root to the "
                "configured trash root. The move is reversible outside this API."
            ),
            inputSchema=ArchiveTaskInput.model_json_schema(),
            annotations=types.ToolAnnotations(
                readOnlyHint=False,
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
        if name == "skills_catalog":
            EmptyInput(**arguments)
            registry = await _skill_registry()
            skills = registry.list()
            result = {
                "skills": [skill.catalog_summary() for skill in skills],
                "categories": sorted({skill.category for skill in skills if skill.category}),
                "count": len(skills),
                "hint": "Use skill_get(skill_id) to load full content and supporting-file metadata.",
            }
        elif name == "skill_get":
            args = SkillGetInput(**arguments)
            registry = await _skill_registry()
            skill = registry.get(args.skill_id)
            content = read_skill_file(skill, "SKILL.md", offset=0, max_bytes=args.max_bytes)
            result = {
                **skill.summary(),
                "frontmatter": skill.frontmatter,
                "provenance_details": skill.provenance_details,
                "relative_dir": skill.relative_dir,
                "sha256": skill.sha256,
                "bytes": skill.bytes,
                "files": list_skill_files(skill),
                "skill_md": content,
            }
        elif name == "skill_read_file":
            args = SkillReadFileInput(**arguments)
            registry = await _skill_registry()
            skill = registry.get(args.skill_id)
            result = read_skill_file(
                skill,
                args.relative_path,
                offset=args.offset,
                max_bytes=args.max_bytes,
            )
        elif name == "list_targets":
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
        elif name == "list_runs":
            args = ListRunsInput(**arguments)
            _run_store().cleanup()
            result = {
                "runs": [
                    record.summary()
                    for record in _run_store().list(
                        status=args.status,
                        task_id=args.task_id,
                        limit=args.limit,
                    )
                ]
            }
        elif name == "get_run":
            args = GetRunInput(**arguments)
            result = _run_store().get(args.run_id).model_dump()
        elif name == "set_run_retained":
            args = RetainRunInput(**arguments)
            result = _run_store().set_retained(args.run_id, args.retained).model_dump()
        elif name == "list_tasks":
            args = ListTasksInput(**arguments)
            _task_store().cleanup()
            result = {
                "tasks": [
                    record.summary()
                    for record in _task_store().list(
                        status=args.status,
                        include_archived=args.include_archived,
                        limit=args.limit,
                    )
                ]
            }
        elif name == "get_task":
            args = GetTaskInput(**arguments)
            result = _task_store().get(args.task_id).model_dump()
        elif name == "create_task":
            args = CreateTaskInput(**arguments)
            result = _task_store().create(
                title=args.title,
                objective=args.objective,
                project_ref=args.project_ref,
                next_action=args.next_action,
                retained=args.retained,
            ).model_dump()
        elif name == "update_task":
            args = UpdateTaskInput(**arguments)
            result = _task_store().update(
                args.task_id,
                title=args.title,
                objective=args.objective,
                project_ref=args.project_ref,
                clear_project_ref=args.clear_project_ref,
                status=args.status,
                next_action=args.next_action,
                clear_next_action=args.clear_next_action,
                retained=args.retained,
            ).model_dump()
        elif name == "archive_task":
            args = ArchiveTaskInput(**arguments)
            result = _task_store().archive(args.task_id).model_dump()
        elif name == "run_script":
            args = RunScriptInput(**arguments)
            script = _tool_registry().get(args.script_id)
            target, connect_timeout, max_output = _target_runtime(
                args.target, script.metadata.timeout_seconds
            )
            ensure_target_compatible(script, target.capabilities)
            run_id, execution = await _tracked_ssh_run(
                operation="run_script",
                target_id=args.target,
                target=target,
                remote_command=build_script_command(script, args.arguments),
                timeout_seconds=script.metadata.timeout_seconds,
                connect_timeout_seconds=connect_timeout,
                max_output_bytes=max_output,
                may_mutate=script.metadata.mutating,
                task_id=args.task_id,
                stdin_text=script.content,
                script_id=script.metadata.id,
                script_source=script.source_id,
                script_sha256=script.sha256,
                argument_names=list(args.arguments),
            )
            result = {
                "run_id": run_id,
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
            run_id, execution = await _tracked_ssh_run(
                operation="run_command",
                target_id=args.target,
                target=target,
                remote_command=args.command,
                timeout_seconds=args.timeout_seconds,
                connect_timeout_seconds=connect_timeout,
                max_output_bytes=max_output,
                may_mutate=True,
                task_id=args.task_id,
            )
            result = {"run_id": run_id, "execution": execution}
        elif name == "run_shell":
            args = ShellInput(**arguments)
            target, connect_timeout, max_output = _target_runtime(
                args.target, args.timeout_seconds
            )
            run_id, execution = await _tracked_ssh_run(
                operation="run_shell",
                target_id=args.target,
                target=target,
                remote_command=f"{args.interpreter} -s --",
                timeout_seconds=args.timeout_seconds,
                connect_timeout_seconds=connect_timeout,
                max_output_bytes=max_output,
                may_mutate=True,
                task_id=args.task_id,
                stdin_text=args.script,
            )
            result = {"run_id": run_id, "execution": execution}
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

    global _config, _run_store_instance, _task_store_instance
    _config = load_config()
    _run_store_instance = RunStore(
        _config.workspace.runs,
        completed_days=_config.retention.runs.completed_days,
    )
    _run_store_instance.cleanup()
    _task_store_instance = TaskStore(
        _config.workspace.tasks,
        _config.workspace.trash,
        archived_days=_config.retention.tasks.archived_days,
    )
    _task_store_instance.cleanup()

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    asyncio.run(run_stdio())

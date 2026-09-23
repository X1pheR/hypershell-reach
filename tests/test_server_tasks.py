from __future__ import annotations

import json

import pytest

from hypershell_reach import server
from hypershell_reach.config import ReachConfig
from hypershell_reach.runs import RunStore
from hypershell_reach.tasks import TaskStore


def _config(tmp_path) -> ReachConfig:
    return ReachConfig.model_validate(
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
async def test_task_lifecycle_and_run_linkage(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs))
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )

    created_content = await server.call_tool(
        "create_task",
        {
            "title": "Example",
            "objective": "Keep continuity",
            "next_action": "Inspect",
            "continuity": {
                "authorization": "Use only the configured example target.",
                "sources": [
                    {
                        "classification": "configured",
                        "reference": "config/example.yaml",
                        "purpose": "Canonical target definition.",
                    }
                ],
                "completed": ["Preflight context captured."],
            },
        },
    )
    created = json.loads(created_content[0].text)
    assert created["continuity"]["authorization"] == "Use only the configured example target."

    async def fake_run_ssh(**kwargs):
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
    run_content = await server.call_tool(
        "run_command",
        {"target": "example", "command": "true", "purpose": "Validate Task-linked execution continuity.", "task_id": created["id"]},
    )
    run = json.loads(run_content[0].text)

    linked_run = server._run_store_instance.get(run["run_id"])
    assert linked_run.task_id == created["id"]
    assert linked_run.idempotent is None

    updated_content = await server.call_tool(
        "update_task",
        {
            "task_id": created["id"],
            "clear_next_action": True,
            "continuity": {
                "authorization": "Use only the configured example target.",
                "completed": ["Preflight context captured.", "Execution completed."],
                "validation": ["Linked run succeeded."],
            },
            "reconcile_mutation": "Linked run succeeded and its postcondition was read back.",
        },
    )
    updated = json.loads(updated_content[0].text)
    assert updated["continuity"]["validation"][0] == "Linked run succeeded."
    assert updated["continuity"]["validation"][-1].startswith("Mutation reconciliation:")
    assert updated["continuity"]["blockers"] == []

    completed_content = await server.call_tool(
        "update_task", {"task_id": created["id"], "status": "completed"}
    )
    completed = json.loads(completed_content[0].text)
    assert completed["status"] == "completed"
    assert completed["archived_at"] is not None
    archived_content = await server.call_tool("archive_task", {"task_id": created["id"]})
    archived = json.loads(archived_content[0].text)
    assert archived["archived_at"] is not None

    rejected = await server.call_tool(
        "run_command",
        {"target": "example", "command": "true", "purpose": "Validate Task-linked execution continuity.", "task_id": created["id"]},
    )
    assert rejected[0].text == f"ERROR: task is archived: {created['id']}"
    assert len(server._run_store_instance.list()) == 1


@pytest.mark.asyncio
async def test_unknown_task_rejects_execution_before_run_creation(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs))
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )

    rejected = await server.call_tool(
        "run_command",
        {"target": "example", "command": "true", "purpose": "Validate rejection of an unknown Task link.", "task_id": "task-20260812T120000000000Z-123456789abc"},
    )

    assert rejected[0].text.startswith("ERROR: unknown task:")
    assert server._run_store_instance.list() == []


@pytest.mark.asyncio
async def test_update_task_expected_revision_rejects_stale_caller(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_task_store_instance", TaskStore(config.workspace.tasks, config.workspace.trash))

    created_content = await server.call_tool(
        "create_task", {"title": "CAS", "objective": "Expose revision CAS through MCP."}
    )
    created = json.loads(created_content[0].text)
    updated_content = await server.call_tool(
        "update_task",
        {"task_id": created["id"], "expected_revision": 1, "title": "Committed"},
    )
    updated = json.loads(updated_content[0].text)
    assert updated["revision"] == 2

    stale = await server.call_tool(
        "update_task",
        {"task_id": created["id"], "expected_revision": 1, "title": "Lost update"},
    )
    assert stale[0].text.startswith("ERROR: stale task revision:")


@pytest.mark.asyncio
async def test_close_task_is_explicit_single_boundary_and_retry_is_idempotent(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_task_store_instance", TaskStore(config.workspace.tasks, config.workspace.trash))

    created_content = await server.call_tool(
        "create_task", {"title": "Close", "objective": "Close with one MCP call."}
    )
    created = json.loads(created_content[0].text)
    arguments = {
        "task_id": created["id"],
        "expected_revision": 1,
        "status": "completed",
        "clear_next_action": True,
    }
    closed_content = await server.call_tool("close_task", arguments)
    closed = json.loads(closed_content[0].text)
    repeated_content = await server.call_tool("close_task", arguments)
    repeated = json.loads(repeated_content[0].text)

    assert closed == repeated
    assert closed["revision"] == 2
    assert closed["status"] == "completed"
    assert closed["archived_at"] is not None
    assert not (tmp_path / "tasks" / created["id"]).exists()
    assert (tmp_path / "trash" / created["id"] / "task.yaml").is_file()


def test_server_task_store_repairs_terminal_residue_on_initialization(tmp_path, monkeypatch) -> None:
    import yaml

    config = _config(tmp_path)
    writable = TaskStore(config.workspace.tasks, config.workspace.trash)
    created = writable.create(title="Residue", objective="Recover at server startup.")
    path = tmp_path / "tasks" / created.id / "task.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload.update(
        status="completed",
        revision=2,
        archived_at="2026-08-22T13:00:00.000000Z",
        updated_at="2026-08-22T13:00:00.000000Z",
    )
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_task_store_instance", None)
    initialized = server._task_store()

    assert initialized.get(created.id).status == "completed"
    assert not (tmp_path / "tasks" / created.id).exists()
    assert (tmp_path / "trash" / created.id / "task.yaml").is_file()


@pytest.mark.asyncio
async def test_task_linked_mutating_run_marks_reconciliation_before_dispatch(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs))
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )

    created_content = await server.call_tool(
        "create_task",
        {
            "title": "Freeze runtime",
            "objective": "Restore every intentionally stopped service before completion.",
        },
    )
    created = json.loads(created_content[0].text)
    blockers_seen_at_dispatch: list[str] = []

    async def fake_run_ssh(**kwargs):
        blockers_seen_at_dispatch.extend(
            server._task_store_instance.get(created["id"]).continuity.blockers
        )
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
    result = await server.call_tool(
        "run_command",
        {
            "target": "example",
            "command": "true",
            "purpose": "Temporarily stop Ignis for a controlled rebuild.",
            "task_id": created["id"],
        },
    )

    assert json.loads(result[0].text)["execution"]["status"] == "succeeded"
    assert len(blockers_seen_at_dispatch) == 1
    assert blockers_seen_at_dispatch[0].startswith("[reach:pending-mutation]")
    assert "Temporarily stop Ignis" in blockers_seen_at_dispatch[0]


@pytest.mark.asyncio
async def test_update_task_reconciles_pending_mutation_with_validation_evidence(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs))
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )

    created_content = await server.call_tool(
        "create_task",
        {"title": "Freeze runtime", "objective": "Restore temporary service state."},
    )
    created = json.loads(created_content[0].text)

    async def fake_run_ssh(**kwargs):
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
    await server.call_tool(
        "run_command",
        {
            "target": "example",
            "command": "true",
            "purpose": "Temporarily stop Ignis for a controlled rebuild.",
            "task_id": created["id"],
        },
    )

    before = server._task_store_instance.get(created["id"])
    assert any(item.startswith("[reach:pending-mutation]") for item in before.continuity.blockers)

    reconciled_content = await server.call_tool(
        "update_task",
        {
            "task_id": created["id"],
            "expected_revision": before.revision,
            "reconcile_mutation": "Home and OCI Ignis observed running with restart_count=0.",
        },
    )
    reconciled = json.loads(reconciled_content[0].text)

    assert not any(
        item.startswith("[reach:pending-mutation]")
        for item in reconciled["continuity"]["blockers"]
    )
    assert reconciled["continuity"]["validation"][-1] == (
        "Mutation reconciliation: Home and OCI Ignis observed running with restart_count=0."
    )


@pytest.mark.asyncio
async def test_task_linked_async_mutation_marks_reconciliation_before_submission(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs))
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )

    created_content = await server.call_tool(
        "create_task",
        {"title": "Async freeze", "objective": "Track mutation before durable submission."},
    )
    created = json.loads(created_content[0].text)
    blockers_seen_at_submission: list[str] = []

    async def fake_submit(submission):
        blockers_seen_at_submission.extend(
            server._task_store_instance.get(created["id"]).continuity.blockers
        )
        return {"run_id": "run-20260913T000000000000Z-abcdef123456", "status": "running"}

    monkeypatch.setattr(server, "_submit_async_execution", fake_submit)
    result = await server.call_tool(
        "start_command",
        {
            "target": "example",
            "command": "true",
            "purpose": "Temporarily stop a service for asynchronous maintenance.",
            "task_id": created["id"],
        },
    )

    assert json.loads(result[0].text)["status"] == "running"
    assert len(blockers_seen_at_submission) == 1
    assert blockers_seen_at_submission[0].startswith("[reach:pending-mutation]")

@pytest.mark.asyncio
async def test_server_exposes_exclusive_task_execution_lease(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )
    created = json.loads((await server.call_tool(
        "create_task",
        {"title": "Lease MCP", "objective": "Expose exclusive ownership."},
    ))[0].text)

    first = json.loads((await server.call_tool(
        "acquire_task_lease",
        {
            "task_id": created["id"],
            "executor_id": "chat-a",
            "scope": "task",
            "lease_seconds": 120,
        },
    ))[0].text)
    second = json.loads((await server.call_tool(
        "acquire_task_lease",
        {
            "task_id": created["id"],
            "executor_id": "chat-b",
            "scope": "task",
            "lease_seconds": 120,
        },
    ))[0].text)

    assert first["acquired"] is True
    assert second["acquired"] is False
    assert second["owner_state"]["executor_id"] == "chat-a"

@pytest.mark.asyncio
async def test_server_refreshes_and_releases_task_execution_lease(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )
    created = json.loads((await server.call_tool(
        "create_task",
        {"title": "Lease lifecycle MCP", "objective": "Refresh and hand off ownership."},
    ))[0].text)
    acquired = json.loads((await server.call_tool(
        "acquire_task_lease",
        {
            "task_id": created["id"],
            "executor_id": "chat-a",
            "scope": "task",
            "lease_seconds": 120,
        },
    ))[0].text)

    refreshed = json.loads((await server.call_tool(
        "refresh_task_lease",
        {
            "task_id": created["id"],
            "lease_id": acquired["lease_id"],
            "lease_seconds": 300,
        },
    ))[0].text)
    assert refreshed["handoff_state"] == "refreshed"

    released = json.loads((await server.call_tool(
        "release_task_lease",
        {"task_id": created["id"], "lease_id": acquired["lease_id"]},
    ))[0].text)
    assert released["handoff_state"] == "released"

    successor = json.loads((await server.call_tool(
        "acquire_task_lease",
        {
            "task_id": created["id"],
            "executor_id": "chat-b",
            "scope": "task",
            "lease_seconds": 120,
        },
    ))[0].text)
    assert successor["acquired"] is True
    assert successor["owner_state"]["executor_id"] == "chat-b"

@pytest.mark.asyncio
async def test_task_lease_blocks_stale_mutating_execution_before_dispatch(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs))
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )
    created = json.loads((await server.call_tool(
        "create_task",
        {"title": "Lease dispatch", "objective": "Reject stale mutation."},
    ))[0].text)
    lease = json.loads((await server.call_tool(
        "acquire_task_lease",
        {
            "task_id": created["id"],
            "executor_id": "chat-owner",
            "scope": "task",
            "lease_seconds": 120,
        },
    ))[0].text)

    dispatches = 0

    async def fake_run_ssh(**kwargs):
        nonlocal dispatches
        dispatches += 1
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

    stale = (await server.call_tool(
        "run_command",
        {
            "target": "example",
            "command": "true",
            "purpose": "Attempt stale mutation.",
            "task_id": created["id"],
        },
    ))[0].text
    assert stale.startswith("ERROR: active task execution lease")
    assert dispatches == 0

    owned = json.loads((await server.call_tool(
        "run_command",
        {
            "target": "example",
            "command": "true",
            "purpose": "Perform owned mutation.",
            "task_id": created["id"],
            "task_lease_id": lease["lease_id"],
        },
    ))[0].text)
    assert owned["execution"]["status"] == "succeeded"
    assert dispatches == 1

@pytest.mark.asyncio
async def test_task_lease_guards_task_update_and_close(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )
    created = json.loads((await server.call_tool(
        "create_task",
        {"title": "Lease edits", "objective": "Guard continuity mutations."},
    ))[0].text)
    lease = json.loads((await server.call_tool(
        "acquire_task_lease",
        {
            "task_id": created["id"],
            "executor_id": "chat-owner",
            "scope": "task",
            "lease_seconds": 120,
        },
    ))[0].text)

    stale_update = (await server.call_tool(
        "update_task",
        {"task_id": created["id"], "title": "stale"},
    ))[0].text
    assert stale_update.startswith("ERROR: active task execution lease")

    owned = json.loads((await server.call_tool(
        "update_task",
        {
            "task_id": created["id"],
            "title": "owned",
            "task_lease_id": lease["lease_id"],
        },
    ))[0].text)
    assert owned["title"] == "owned"

    stale_close = (await server.call_tool(
        "close_task",
        {"task_id": created["id"], "status": "completed"},
    ))[0].text
    assert stale_close.startswith("ERROR: active task execution lease")

    closed = json.loads((await server.call_tool(
        "close_task",
        {
            "task_id": created["id"],
            "status": "completed",
            "task_lease_id": lease["lease_id"],
        },
    ))[0].text)
    assert closed["status"] == "completed"

@pytest.mark.asyncio
async def test_task_lease_guards_async_start_command_before_submission(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", RunStore(config.workspace.runs))
    monkeypatch.setattr(
        server,
        "_task_store_instance",
        TaskStore(config.workspace.tasks, config.workspace.trash),
    )
    created = json.loads((await server.call_tool(
        "create_task",
        {"title": "Lease async", "objective": "Reject stale durable submission."},
    ))[0].text)
    lease = json.loads((await server.call_tool(
        "acquire_task_lease",
        {
            "task_id": created["id"],
            "executor_id": "chat-owner",
            "scope": "task",
            "lease_seconds": 120,
        },
    ))[0].text)

    submissions = 0

    async def fake_submit(submission):
        nonlocal submissions
        submissions += 1
        return {"run_id": "run-20260923T100000000000Z-abcdef123456", "status": "running"}

    monkeypatch.setattr(server, "_submit_async_execution", fake_submit)

    stale = (await server.call_tool(
        "start_command",
        {
            "target": "example",
            "command": "true",
            "purpose": "Attempt stale durable mutation.",
            "task_id": created["id"],
        },
    ))[0].text
    assert stale.startswith("ERROR: active task execution lease")
    assert submissions == 0

    owned = json.loads((await server.call_tool(
        "start_command",
        {
            "target": "example",
            "command": "true",
            "purpose": "Submit owned durable mutation.",
            "task_id": created["id"],
            "task_lease_id": lease["lease_id"],
        },
    ))[0].text)
    assert owned["status"] == "running"
    assert submissions == 1

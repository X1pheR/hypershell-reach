from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from hats_mcp import server
from hats_mcp.config import HATSConfig
from hats_mcp.runs import (
    PURPOSE_MAX_LENGTH,
    RESULT_SUMMARY_MAX_LENGTH,
    RESULT_SUMMARY_TRUNCATION_SUFFIX,
    RunRecord,
    RunStore,
    _bounded_result_summary,
)


def _config(tmp_path) -> HATSConfig:
    return HATSConfig.model_validate(
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


def _execution(status: str = "succeeded") -> dict[str, object]:
    return {
        "target": "example",
        "status": status,
        "exit_code": 0 if status == "succeeded" else 255,
        "timed_out": status == "timeout",
        "duration_ms": 20,
        "stdout": {"text": "TOP-SECRET-STDOUT", "bytes": 17, "truncated": False},
        "stderr": {"text": "TOP-SECRET-STDERR", "bytes": 17, "truncated": True},
    }


def _legacy_v1_payload(run_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": run_id,
        "operation": "run_command",
        "target": "example",
        "timeout_seconds": 30,
        "may_mutate": False,
        "started_at": "2026-08-22T12:00:00.000000Z",
        "ended_at": "2026-08-22T12:00:01.000000Z",
        "status": "succeeded",
    }


@pytest.mark.asyncio
async def test_agent_run_persists_trimmed_purpose_and_returns_it_from_run_apis(
    tmp_path, monkeypatch
) -> None:
    config = _config(tmp_path)
    store = RunStore(config.workspace.runs)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", store)

    async def fake_run_ssh(**kwargs):
        assert kwargs["remote_command"] == "printf safe"
        return _execution()

    monkeypatch.setattr(server, "run_ssh", fake_run_ssh)
    response = await server.call_tool(
        "run_command",
        {
            "target": "example",
            "command": "printf safe",
            "purpose": "  Verify the selected runtime can execute a bounded probe.  ",
        },
    )
    run_id = json.loads(response[0].text)["run_id"]
    record = store.get(run_id)

    assert record.schema_version == 2
    assert record.purpose == "Verify the selected runtime can execute a bounded probe."
    assert record.operation == "run_command"
    assert record.purpose != record.operation
    assert record.status == "succeeded"
    assert record.result_summary is not None
    assert "Execution succeeded" in record.result_summary
    assert "Output content was not persisted" in record.result_summary

    listed = json.loads((await server.call_tool("list_runs", {}))[0].text)["runs"][0]
    fetched = json.loads((await server.call_tool("get_run", {"run_id": run_id}))[0].text)
    assert listed["purpose"] == record.purpose
    assert listed["result_summary"] == record.result_summary
    assert fetched["purpose"] == record.purpose
    assert fetched["result_summary"] == record.result_summary


def test_purpose_survives_timeout_local_failure_and_startup_recovery(tmp_path) -> None:
    timeout_store = RunStore(tmp_path / "timeout")
    timed = timeout_store.create(
        operation="run_command",
        target="example",
        purpose="Determine whether the bounded remote probe completes.",
        timeout_seconds=30,
        may_mutate=True,
    )
    timed = timeout_store.finish(timed.id, _execution("timeout"))
    assert timed.purpose == "Determine whether the bounded remote probe completes."
    assert timed.status == "timeout"
    assert timed.result_summary is not None and "timed out after 30 seconds" in timed.result_summary
    assert timed.ambiguous is True

    local_store = RunStore(tmp_path / "local")
    failed = local_store.create(
        operation="run_shell",
        target="example",
        purpose="Validate local execution setup before remote work.",
        timeout_seconds=30,
        may_mutate=True,
    )
    failed = local_store.fail_local(failed.id, "RuntimeError")
    assert failed.purpose == "Validate local execution setup before remote work."
    assert failed.status == "local_error"
    assert failed.result_summary is not None and "RuntimeError" in failed.result_summary

    recovery_root = tmp_path / "recovery"
    first = RunStore(recovery_root)
    running = first.create(
        operation="run_shell",
        target="example",
        purpose="Keep operator intent across server restart recovery.",
        timeout_seconds=30,
        may_mutate=True,
    )
    recovered = RunStore(recovery_root).get(running.id)
    assert recovered.status == "interrupted"
    assert recovered.purpose == "Keep operator intent across server restart recovery."
    assert recovered.error_type == "ServerRestart"
    assert recovered.result_summary is not None and "interrupted" in recovered.result_summary


def test_historical_v1_run_remains_readable_and_is_not_silently_rewritten(tmp_path) -> None:
    run_id = "run-20260822T120000000000Z-abcdefabcdef"
    path = tmp_path / f"{run_id}.json"
    payload = _legacy_v1_payload(run_id)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    before = path.read_bytes()

    store = RunStore(tmp_path)
    record = store.get(run_id)

    assert record.schema_version == 1
    assert record.purpose is None
    assert record.result_summary is None
    assert record.summary()["purpose"] is None
    assert record.summary()["result_summary"] is None
    assert path.read_bytes() == before

    retained = store.set_retained(run_id, True)
    rewritten = json.loads(path.read_text(encoding="utf-8"))
    assert retained.schema_version == 1
    assert rewritten["schema_version"] == 1
    assert "purpose" not in rewritten
    assert "result_summary" not in rewritten


def test_v1_schema_rejects_new_fields_instead_of_inventing_legacy_meaning() -> None:
    payload = _legacy_v1_payload("run-20260822T120000000000Z-abcdefabcdef")
    payload["purpose"] = "Not valid in v1"
    with pytest.raises(ValidationError, match="schema v1"):
        RunRecord.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "purpose",
    [
        None,
        "",
        "   ",
        "two\nlines",
        "x" * (PURPOSE_MAX_LENGTH + 1),
        7,
    ],
)
async def test_agent_execution_rejects_missing_invalid_or_oversized_purpose(
    tmp_path, monkeypatch, purpose
) -> None:
    config = _config(tmp_path)
    store = RunStore(config.workspace.runs)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", store)
    arguments = {"target": "example", "command": "true"}
    if purpose is not None:
        arguments["purpose"] = purpose

    response = await server.call_tool("run_command", arguments)

    assert response[0].text.startswith("ERROR: invalid tool input:")
    assert store.list() == []


def test_result_summary_is_bounded_and_rejects_unbounded_persisted_values() -> None:
    bounded = _bounded_result_summary("x" * 2_000)
    assert len(bounded) == RESULT_SUMMARY_MAX_LENGTH
    assert bounded.endswith(RESULT_SUMMARY_TRUNCATION_SUFFIX)

    payload = {
        "schema_version": 2,
        "id": "run-20260822T120000000000Z-abcdefabcdef",
        "operation": "run_command",
        "target": "example",
        "purpose": "Validate persisted diagnostic size bounds.",
        "result_summary": "x" * (RESULT_SUMMARY_MAX_LENGTH + 1),
        "timeout_seconds": 30,
        "may_mutate": False,
        "started_at": "2026-08-22T12:00:00.000000Z",
    }
    with pytest.raises(ValidationError):
        RunRecord.model_validate(payload)


@pytest.mark.asyncio
async def test_raw_execution_content_and_secret_sensitive_fields_remain_absent_from_persistence(
    tmp_path, monkeypatch
) -> None:
    config = _config(tmp_path)
    store = RunStore(config.workspace.runs)
    monkeypatch.setattr(server, "_config", config, raising=False)
    monkeypatch.setattr(server, "_run_store_instance", store)

    async def fake_run_ssh(**kwargs):
        return _execution()

    monkeypatch.setattr(server, "run_ssh", fake_run_ssh)
    command_secret = "COMMAND-SECRET-8421"
    script_secret = "SCRIPT-SECRET-1754"

    command_result = json.loads(
        (
            await server.call_tool(
                "run_command",
                {
                    "target": "example",
                    "command": f"printf {command_secret}",
                    "purpose": "Verify command persistence boundaries.",
                },
            )
        )[0].text
    )
    shell_result = json.loads(
        (
            await server.call_tool(
                "run_shell",
                {
                    "target": "example",
                    "interpreter": "bash",
                    "script": f"printf {script_secret}",
                    "purpose": "Verify shell persistence boundaries.",
                },
            )
        )[0].text
    )

    persisted = "\n".join(
        (tmp_path / "runs" / f"{run_id}.json").read_text(encoding="utf-8")
        for run_id in (command_result["run_id"], shell_result["run_id"])
    )
    assert command_secret not in persisted
    assert script_secret not in persisted
    assert "TOP-SECRET-STDOUT" not in persisted
    assert "TOP-SECRET-STDERR" not in persisted
    assert '"command"' not in persisted
    assert '"script"' not in persisted
    assert '"arguments"' not in persisted
    assert '"environment"' not in persisted
    assert '"env"' not in persisted


def test_run_task_relation_remains_optional_and_reverse_derived_only_from_task_id(tmp_path) -> None:
    store = RunStore(tmp_path)
    unlinked = store.create(
        operation="run_command",
        target="example",
        purpose="Validate an execution without Task continuity.",
        timeout_seconds=30,
        may_mutate=False,
    )
    linked = store.create(
        operation="run_command",
        target="example",
        purpose="Validate reverse Task-to-Run derivation.",
        timeout_seconds=30,
        may_mutate=False,
        task_id="task-example",
    )

    assert unlinked.task_id is None
    assert linked.task_id == "task-example"
    assert [record.id for record in store.list(task_id="task-example")] == [linked.id]
    raw = json.loads((tmp_path / f"{linked.id}.json").read_text(encoding="utf-8"))
    assert "linked_run_ids" not in raw
    assert "runs" not in raw

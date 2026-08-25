from __future__ import annotations

import asyncio

import pytest

from hypershell_reach import executor
from hypershell_reach.config import ReachConfig
from hypershell_reach.runs import RunStore


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
            "executor": {"socket_path": str(tmp_path / "executor.sock")},
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


def _submission() -> executor.ExecutionSubmission:
    return executor.ExecutionSubmission(
        operation="run_command",
        target="example",
        remote_command="sleep 1",
        timeout_seconds=300,
        purpose="Prove detached executor ownership.",
        may_mutate=False,
    )


@pytest.mark.asyncio
async def test_submission_connection_can_close_before_job_finishes(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    release = asyncio.Event()

    async def fake_run_ssh(**kwargs):
        await release.wait()
        return {
            "target": kwargs["target_id"],
            "status": "succeeded",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 1000,
            "stdout": {"text": "SECRET-WORKER-OUTPUT", "bytes": 20, "truncated": False},
            "stderr": {"text": "", "bytes": 0, "truncated": False},
        }

    monkeypatch.setattr(executor, "run_ssh", fake_run_ssh)
    service = executor.ExecutorService(config)
    await service.start()
    try:
        accepted = await executor.submit_execution(config, _submission())
        run_id = accepted["run_id"]
        assert accepted["status"] == "running"
        assert accepted["execution_mode"] == "async"
        assert service.store.get(run_id).status == "running"

        # submit_execution has already closed its one-request Unix connection here.
        release.set()
        await service.wait_for_idle()

        finished = service.store.get(run_id)
        assert finished.status == "succeeded"
        assert finished.exit_code == 0
        assert finished.execution_mode == "async"
        assert "SECRET-WORKER-OUTPUT" not in (tmp_path / "runs" / f"{run_id}.json").read_text()
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_explicit_cancellation_is_deterministic_and_idempotent(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    started = asyncio.Event()

    async def fake_run_ssh(**kwargs):
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(executor, "run_ssh", fake_run_ssh)
    service = executor.ExecutorService(config)
    await service.start()
    try:
        accepted = await executor.submit_execution(config, _submission())
        run_id = accepted["run_id"]
        await started.wait()

        cancelled = await executor.cancel_execution(config, run_id)
        assert cancelled == {"run_id": run_id, "status": "interrupted", "cancelled": True}
        record = service.store.get(run_id)
        assert record.status == "interrupted"
        assert record.error_type == "ExplicitCancellation"
        assert record.ambiguous is False

        duplicate = await executor.cancel_execution(config, run_id)
        assert duplicate == {"run_id": run_id, "status": "interrupted", "cancelled": False}
        assert service.store.get(run_id).error_type == "ExplicitCancellation"
    finally:
        await service.stop()


def test_product_has_single_installed_console_entrypoint() -> None:
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"] == {"reach": "hypershell_reach.service:main"}


@pytest.mark.asyncio
async def test_executor_startup_reconciles_only_async_runs(tmp_path) -> None:
    config = _config(tmp_path)
    seed = RunStore(config.workspace.runs, reconcile_modes=set())
    sync_run = seed.create(
        operation="run_command",
        target="example",
        timeout_seconds=30,
        may_mutate=False,
        execution_mode="sync",
    )
    async_run = seed.create(
        operation="run_command",
        target="example",
        timeout_seconds=300,
        may_mutate=True,
        execution_mode="async",
    )

    service = executor.ExecutorService(config)
    await service.start()
    try:
        assert service.store.get(sync_run.id).status == "running"
        recovered = service.store.get(async_run.id)
        assert recovered.status == "interrupted"
        assert recovered.error_type == "ExecutorRestart"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_executor_stop_removes_socket_and_reuses_fixed_lease_file(tmp_path) -> None:
    config = _config(tmp_path)
    service = executor.ExecutorService(config)
    await service.start()
    socket_path = service.socket_path
    lock_path = type(socket_path)(f"{socket_path}.lock")
    assert socket_path.exists()
    assert lock_path.exists()

    await service.stop()
    assert not socket_path.exists()
    assert lock_path.exists()

    replacement = executor.ExecutorService(config)
    await replacement.start()
    try:
        assert replacement.socket_path.exists()
    finally:
        await replacement.stop()


@pytest.mark.asyncio
async def test_requester_disconnect_before_response_does_not_cancel_accepted_job(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    release = asyncio.Event()

    async def fake_run_ssh(**kwargs):
        await release.wait()
        return {
            "target": kwargs["target_id"],
            "status": "succeeded",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 1000,
            "stdout": {"text": "ignored", "bytes": 7, "truncated": False},
            "stderr": {"text": "", "bytes": 0, "truncated": False},
        }

    monkeypatch.setattr(executor, "run_ssh", fake_run_ssh)
    service = executor.ExecutorService(config)
    await service.start()
    try:
        _, writer = await asyncio.open_unix_connection(config.executor.socket_path)
        request = executor.ExecutorRequest(action="submit", submission=_submission())
        writer.write(request.model_dump_json().encode("utf-8") + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

        for _ in range(100):
            records = service.store.list()
            if records:
                break
            await asyncio.sleep(0.01)
        assert len(records) == 1
        run_id = records[0].id
        assert service.store.get(run_id).status == "running"

        release.set()
        await service.wait_for_idle()
        assert service.store.get(run_id).status == "succeeded"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_executor_refuses_to_unlink_non_socket_path(tmp_path) -> None:
    config = _config(tmp_path)
    socket_path = type(tmp_path)(config.executor.socket_path)
    socket_path.write_text("do not delete", encoding="utf-8")
    service = executor.ExecutorService(config)

    with pytest.raises(RuntimeError, match="not a Unix socket"):
        await service.start()

    assert socket_path.read_text(encoding="utf-8") == "do not delete"


@pytest.mark.asyncio
async def test_cancel_detects_completed_task_without_terminal_run_as_ownership_loss(tmp_path) -> None:
    config = _config(tmp_path)
    service = executor.ExecutorService(config)
    await service.start()
    try:
        record = service.store.create(
            operation="run_command",
            target="example",
            timeout_seconds=300,
            may_mutate=True,
            execution_mode="async",
        )
        finished_task = asyncio.create_task(asyncio.sleep(0))
        await finished_task
        service._tasks[record.id] = finished_task

        result = await service._cancel(record.id)

        assert result == {"run_id": record.id, "status": "interrupted", "cancelled": False}
        recovered = service.store.get(record.id)
        assert recovered.error_type == "ExecutorOwnershipLost"
        assert recovered.ambiguous is True
    finally:
        service._tasks.pop(record.id, None)
        await service.stop()


@pytest.mark.asyncio
async def test_unexpected_executor_error_marks_run_unknown(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)

    async def fake_run_ssh(**kwargs):
        raise KeyError("unexpected")

    monkeypatch.setattr(executor, "run_ssh", fake_run_ssh)
    service = executor.ExecutorService(config)
    await service.start()
    try:
        accepted = await executor.submit_execution(config, _submission())
        await service.wait_for_idle()
        record = service.store.get(accepted["run_id"])
        assert record.status == "unknown"
        assert record.error_type == "KeyError"
    finally:
        await service.stop()

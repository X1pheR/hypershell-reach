from __future__ import annotations

import asyncio
import fcntl
import json
import os
import stat
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .config import ReachConfig, load_config
from .execution import run_ssh
from .runs import RunOperation, RunStore

_MAX_MESSAGE_BYTES = 2_097_152


class ExecutionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: RunOperation
    target: str = Field(min_length=1, max_length=63)
    remote_command: str = Field(min_length=1, max_length=262_144)
    stdin_text: str | None = Field(default=None, max_length=1_048_576)
    timeout_seconds: int = Field(ge=1, le=900)
    purpose: str = Field(min_length=1, max_length=512)
    may_mutate: bool
    idempotent: bool | None = None
    task_id: str | None = None
    script_id: str | None = None
    script_source: str | None = None
    script_sha256: str | None = None
    argument_names: list[str] = Field(default_factory=list)


class ExecutorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["submit", "cancel"]
    submission: ExecutionSubmission | None = None
    run_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "ExecutorRequest":
        if self.action == "submit":
            if self.submission is None or self.run_id is not None:
                raise ValueError("submit requires submission only")
        elif self.run_id is None or self.submission is not None:
            raise ValueError("cancel requires run_id only")
        return self


class ExecutorService:
    def __init__(self, config: ReachConfig, *, serve_socket: bool = True) -> None:
        if serve_socket and config.executor.socket_path is None:
            raise RuntimeError("Hypershell Reach async executor is not configured")
        self.config = config
        self.serve_socket = serve_socket
        self.socket_path = Path(config.executor.socket_path) if config.executor.socket_path is not None else None
        self._server: asyncio.AbstractServer | None = None
        self._store: RunStore | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_reasons: dict[str, str] = {}
        self._semaphore = asyncio.Semaphore(config.executor.max_concurrency)
        self._lock_handle = None

    @property
    def store(self) -> RunStore:
        if self._store is None:
            raise RuntimeError("executor service is not started")
        return self._store

    async def start(self) -> None:
        if self._store is not None:
            return
        if not self.serve_socket:
            self._store = RunStore(
                self.config.workspace.runs,
                completed_days=self.config.retention.runs.completed_days,
                reconcile_modes={"async"},
            )
            return
        assert self.socket_path is not None
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        lock_path = Path(f"{self.socket_path}.lock")
        self._lock_handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_handle.close()
            self._lock_handle = None
            raise RuntimeError("another Hypershell Reach executor already owns the configured socket") from exc

        try:
            try:
                socket_mode = self.socket_path.lstat().st_mode
            except FileNotFoundError:
                socket_mode = None
            if socket_mode is not None:
                if not stat.S_ISSOCK(socket_mode):
                    raise RuntimeError("configured executor socket path exists and is not a Unix socket")
                self.socket_path.unlink()

            self._store = RunStore(
                self.config.workspace.runs,
                completed_days=self.config.retention.runs.completed_days,
                reconcile_modes={"async"},
            )
            self._server = await asyncio.start_unix_server(
                self._handle_client,
                path=str(self.socket_path),
                limit=_MAX_MESSAGE_BYTES + 1,
            )
            os.chmod(self.socket_path, 0o600)
        except Exception:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
                self._server = None
            if self._lock_handle is not None:
                try:
                    fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
                finally:
                    self._lock_handle.close()
                    self._lock_handle = None
            raise

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()

        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if self.serve_socket and self.socket_path is not None:
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
        self._store = None

        if self._lock_handle is not None:
            try:
                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_handle.close()
                self._lock_handle = None

    async def wait_for_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        response: dict[str, object]
        try:
            raw = await reader.readline()
            if not raw or len(raw) > _MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
                raise ValueError("invalid executor request framing")
            payload = json.loads(raw)
            request = ExecutorRequest.model_validate(payload)
            if request.action == "submit":
                assert request.submission is not None
                response = {"ok": True, **self._submit(request.submission)}
            else:
                assert request.run_id is not None
                response = {"ok": True, **(await self._cancel(request.run_id))}
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            response = {"ok": False, "error": type(exc).__name__}
        except Exception as exc:  # caller gets only an allowlisted error type
            response = {"ok": False, "error": type(exc).__name__}

        writer.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def submit(self, submission: ExecutionSubmission) -> dict[str, object]:
        return self._submit(submission)

    async def cancel(self, run_id: str) -> dict[str, object]:
        return await self._cancel(run_id)

    def _submit(self, submission: ExecutionSubmission) -> dict[str, object]:
        target = self.config.enabled_target(submission.target)
        max_timeout = self.config.resolved_max_timeout(target)
        if submission.timeout_seconds > max_timeout:
            raise ValueError(
                f"requested timeout {submission.timeout_seconds}s exceeds target limit {max_timeout}s"
            )

        record = self.store.create(
            operation=submission.operation,
            target=submission.target,
            timeout_seconds=submission.timeout_seconds,
            may_mutate=submission.may_mutate,
            execution_mode="async",
            purpose=submission.purpose,
            idempotent=submission.idempotent,
            task_id=submission.task_id,
            script_id=submission.script_id,
            script_source=submission.script_source,
            script_sha256=submission.script_sha256,
            argument_names=submission.argument_names,
        )
        task = asyncio.create_task(self._execute(record.id, submission), name=f"reach-async:{record.id}")
        self._tasks[record.id] = task
        task.add_done_callback(lambda _task, run_id=record.id: self._tasks.pop(run_id, None))
        return {
            "run_id": record.id,
            "status": record.status,
            "execution_mode": record.execution_mode,
        }

    async def _cancel(self, run_id: str) -> dict[str, object]:
        record = self.store.get(run_id)
        if record.execution_mode != "async":
            raise ValueError("only async runs are owned by the executor")
        if record.status != "running":
            return {"run_id": run_id, "status": record.status, "cancelled": False}
        task = self._tasks.get(run_id)
        if task is None or task.done():
            updated = self.store.interrupt(run_id, error_type="ExecutorOwnershipLost")
            return {"run_id": run_id, "status": updated.status, "cancelled": False}
        self._cancel_reasons[run_id] = "ExplicitCancellation"
        if not task.cancel():
            self._cancel_reasons.pop(run_id, None)
            current = self.store.get(run_id)
            if current.status == "running":
                current = self.store.interrupt(run_id, error_type="ExecutorOwnershipLost")
            return {"run_id": run_id, "status": current.status, "cancelled": False}
        await asyncio.gather(task, return_exceptions=True)
        updated = self.store.get(run_id)
        cancelled = updated.status == "interrupted" and updated.error_type == "ExplicitCancellation"
        return {"run_id": run_id, "status": updated.status, "cancelled": cancelled}

    async def _execute(self, run_id: str, submission: ExecutionSubmission) -> None:
        async with self._semaphore:
            try:
                target = self.config.enabled_target(submission.target)
                execution = await run_ssh(
                    target_id=submission.target,
                    target=target,
                    connect_timeout_seconds=self.config.resolved_connect_timeout(target),
                    timeout_seconds=submission.timeout_seconds,
                    max_output_bytes=self.config.resolved_max_output(target),
                    remote_command=submission.remote_command,
                    stdin_text=submission.stdin_text,
                )
            except asyncio.CancelledError:
                reason = self._cancel_reasons.pop(run_id, "ExecutorShutdown")
                self.store.interrupt(run_id, error_type=reason)
                raise
            except (ValueError, RuntimeError) as exc:
                self.store.fail_local(run_id, error_type=type(exc).__name__)
            except Exception as exc:
                self.store.mark_unknown(run_id, error_type=type(exc).__name__)
            else:
                self.store.finish(run_id, execution)


async def submit_execution(config: ReachConfig, submission: ExecutionSubmission) -> dict[str, object]:
    socket_path = config.executor.socket_path
    if socket_path is None:
        raise RuntimeError("Hypershell Reach async executor is not configured")
    timeout = config.executor.submission_timeout_seconds
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path), timeout=timeout
        )
    except (OSError, TimeoutError) as exc:
        raise RuntimeError("Hypershell Reach async executor is unavailable") from exc

    request = ExecutorRequest(action="submit", submission=submission)
    writer.write(request.model_dump_json().encode("utf-8") + b"\n")
    try:
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
    finally:
        writer.close()
        await writer.wait_closed()

    if not raw or len(raw) > _MAX_MESSAGE_BYTES:
        raise RuntimeError("Hypershell Reach async executor returned an invalid response")
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Hypershell Reach async executor returned an invalid response") from exc
    if response.get("ok") is not True:
        error_type = str(response.get("error") or "ExecutorError")[:100]
        raise RuntimeError(f"Hypershell Reach async executor rejected the submission ({error_type})")
    return {
        "run_id": response["run_id"],
        "status": response["status"],
        "execution_mode": response["execution_mode"],
    }


async def cancel_execution(config: ReachConfig, run_id: str) -> dict[str, object]:
    socket_path = config.executor.socket_path
    if socket_path is None:
        raise RuntimeError("Hypershell Reach async executor is not configured")
    timeout = config.executor.submission_timeout_seconds
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path), timeout=timeout
        )
    except (OSError, TimeoutError) as exc:
        raise RuntimeError("Hypershell Reach async executor is unavailable") from exc

    request = ExecutorRequest(action="cancel", run_id=run_id)
    writer.write(request.model_dump_json().encode("utf-8") + b"\n")
    try:
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
    finally:
        writer.close()
        await writer.wait_closed()

    if not raw or len(raw) > _MAX_MESSAGE_BYTES:
        raise RuntimeError("Hypershell Reach async executor returned an invalid response")
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Hypershell Reach async executor returned an invalid response") from exc
    if response.get("ok") is not True:
        error_type = str(response.get("error") or "ExecutorError")[:100]
        raise RuntimeError(f"Hypershell Reach async executor rejected cancellation ({error_type})")
    return {
        "run_id": response["run_id"],
        "status": response["status"],
        "cancelled": bool(response["cancelled"]),
    }

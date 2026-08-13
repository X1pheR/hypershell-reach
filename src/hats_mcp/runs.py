from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

RunStatus = Literal[
    "running",
    "succeeded",
    "remote_error",
    "transport_error",
    "timeout",
    "local_error",
    "interrupted",
    "unknown",
]
RunOperation = Literal["run_command", "run_shell", "run_script"]

_RUN_ID = re.compile(r"^run-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")
_TERMINAL_CLEANUP_STATUSES = {"succeeded", "remote_error", "transport_error", "timeout", "local_error"}
_AMBIGUOUS_STATUSES = {"transport_error", "timeout", "interrupted", "unknown"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def new_run_id(now: datetime | None = None) -> str:
    value = (now or utc_now()).astimezone(timezone.utc)
    return f"run-{value.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:12]}"


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str
    operation: RunOperation
    target: str
    task_id: str | None = None
    script_id: str | None = None
    script_source: str | None = None
    script_sha256: str | None = None
    argument_names: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(ge=1, le=900)
    may_mutate: bool
    idempotent: bool | None = None
    retained: bool = False
    started_at: str
    ended_at: str | None = None
    status: RunStatus = "running"
    ambiguous: bool = False
    exit_code: int | None = None
    timed_out: bool | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    stdout_bytes: int | None = Field(default=None, ge=0)
    stderr_bytes: int | None = Field(default=None, ge=0)
    stdout_truncated: bool | None = None
    stderr_truncated: bool | None = None
    error_type: str | None = Field(default=None, max_length=200)

    def summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "operation": self.operation,
            "target": self.target,
            "task_id": self.task_id,
            "script_id": self.script_id,
            "may_mutate": self.may_mutate,
            "idempotent": self.idempotent,
            "status": self.status,
            "ambiguous": self.ambiguous,
            "retained": self.retained,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


class RunStore:
    def __init__(
        self,
        root: str | Path,
        *,
        completed_days: int | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.root = Path(root)
        self.completed_days = completed_days
        self._now = now
        self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
        self.reconcile_incomplete()

    def _path(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run ID")
        return self.root / f"{run_id}.json"

    def _atomic_write(self, record: RunRecord) -> None:
        path = self._path(record.id)
        temporary = self.root / f".{record.id}.{uuid4().hex}.tmp"
        payload = json.dumps(record.model_dump(), indent=2, sort_keys=True) + "\n"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _read_path(self, path: Path) -> RunRecord:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return RunRecord.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise RuntimeError(f"invalid run record: {path.name}") from exc

    def create(
        self,
        *,
        operation: RunOperation,
        target: str,
        timeout_seconds: int,
        may_mutate: bool,
        idempotent: bool | None = None,
        task_id: str | None = None,
        script_id: str | None = None,
        script_source: str | None = None,
        script_sha256: str | None = None,
        argument_names: list[str] | None = None,
    ) -> RunRecord:
        now = self._now()
        record = RunRecord(
            id=new_run_id(now),
            operation=operation,
            target=target,
            task_id=task_id,
            script_id=script_id,
            script_source=script_source,
            script_sha256=script_sha256,
            argument_names=sorted(argument_names or []),
            timeout_seconds=timeout_seconds,
            may_mutate=may_mutate,
            idempotent=idempotent,
            started_at=format_timestamp(now),
        )
        self._atomic_write(record)
        return record

    def get(self, run_id: str) -> RunRecord:
        path = self._path(run_id)
        if not path.is_file():
            raise ValueError(f"unknown run: {run_id}")
        return self._read_path(path)

    def list(
        self,
        *,
        status: RunStatus | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[RunRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("run list limit must be between 1 and 500")
        records = [self._read_path(path) for path in self.root.glob("run-*.json") if path.is_file()]
        if status is not None:
            records = [record for record in records if record.status == status]
        if task_id is not None:
            records = [record for record in records if record.task_id == task_id]
        records.sort(key=lambda record: (record.started_at, record.id), reverse=True)
        return records[:limit]

    def finish(self, run_id: str, execution: dict[str, Any]) -> RunRecord:
        record = self.get(run_id)
        if record.status != "running":
            raise RuntimeError(f"run is not running: {run_id}")
        status = execution.get("status")
        if status not in {"succeeded", "remote_error", "transport_error", "timeout"}:
            raise RuntimeError(f"unsupported execution status for run {run_id}: {status}")

        stdout = execution.get("stdout") if isinstance(execution.get("stdout"), dict) else {}
        stderr = execution.get("stderr") if isinstance(execution.get("stderr"), dict) else {}
        ended = self._now()
        updated = record.model_copy(
            update={
                "ended_at": format_timestamp(ended),
                "status": status,
                "ambiguous": bool(record.may_mutate and status in _AMBIGUOUS_STATUSES),
                "exit_code": execution.get("exit_code"),
                "timed_out": bool(execution.get("timed_out")),
                "duration_ms": execution.get("duration_ms"),
                "stdout_bytes": stdout.get("bytes"),
                "stderr_bytes": stderr.get("bytes"),
                "stdout_truncated": stdout.get("truncated"),
                "stderr_truncated": stderr.get("truncated"),
            }
        )
        self._atomic_write(updated)
        return updated

    def fail_local(self, run_id: str, error_type: str) -> RunRecord:
        return self._finish_without_execution(run_id, status="local_error", error_type=error_type)

    def interrupt(self, run_id: str, error_type: str = "CancelledError") -> RunRecord:
        return self._finish_without_execution(run_id, status="interrupted", error_type=error_type)

    def mark_unknown(self, run_id: str, error_type: str) -> RunRecord:
        return self._finish_without_execution(run_id, status="unknown", error_type=error_type)

    def _finish_without_execution(
        self,
        run_id: str,
        *,
        status: Literal["local_error", "interrupted", "unknown"],
        error_type: str,
    ) -> RunRecord:
        record = self.get(run_id)
        if record.status != "running":
            return record
        updated = record.model_copy(
            update={
                "ended_at": format_timestamp(self._now()),
                "status": status,
                "ambiguous": bool(record.may_mutate and status in _AMBIGUOUS_STATUSES),
                "error_type": error_type[:200],
            }
        )
        self._atomic_write(updated)
        return updated

    def set_retained(self, run_id: str, retained: bool) -> RunRecord:
        record = self.get(run_id)
        updated = record.model_copy(update={"retained": retained})
        self._atomic_write(updated)
        return updated

    def reconcile_incomplete(self) -> int:
        reconciled = 0
        for path in sorted(self.root.glob("run-*.json")):
            if not path.is_file():
                continue
            record = self._read_path(path)
            if record.status == "running":
                self.interrupt(record.id, error_type="ServerRestart")
                reconciled += 1
        return reconciled

    def cleanup(self) -> list[str]:
        if self.completed_days is None:
            return []
        cutoff = self._now() - timedelta(days=self.completed_days)
        removed: list[str] = []
        for path in sorted(self.root.glob("run-*.json")):
            if not path.is_file():
                continue
            record = self._read_path(path)
            if record.status not in _TERMINAL_CLEANUP_STATUSES:
                continue
            if record.ambiguous or record.retained or record.ended_at is None:
                continue
            if parse_timestamp(record.ended_at) > cutoff:
                continue
            path.unlink()
            removed.append(record.id)
        return removed

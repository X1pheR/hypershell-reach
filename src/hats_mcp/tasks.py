from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

TaskStatus = Literal["active", "partial", "blocked", "completed", "cancelled"]

_TASK_ID = re.compile(r"^task-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")
_OPEN_STATUSES = {"active", "partial", "blocked"}
_TERMINAL_STATUSES = {"completed", "cancelled"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def new_task_id(now: datetime | None = None) -> str:
    value = (now or utc_now()).astimezone(timezone.utc)
    return f"task-{value.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:12]}"


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=4_000)
    project_ref: str | None = Field(default=None, min_length=1, max_length=256)
    status: TaskStatus = "active"
    next_action: str | None = Field(default=None, min_length=1, max_length=2_000)
    retained: bool = False
    created_at: str
    updated_at: str
    archived_at: str | None = None

    def summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "project_ref": self.project_ref,
            "status": self.status,
            "next_action": self.next_action,
            "retained": self.retained,
            "archived": self.archived_at is not None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
        }


class TaskStore:
    def __init__(
        self,
        tasks_root: str | Path,
        trash_root: str | Path,
        *,
        archived_days: int | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.tasks_root = Path(tasks_root)
        self.trash_root = Path(trash_root)
        self.archived_days = archived_days
        self._now = now
        self.tasks_root.mkdir(parents=True, exist_ok=True, mode=0o750)
        self.trash_root.mkdir(parents=True, exist_ok=True, mode=0o750)

    def _validate_task_id(self, task_id: str) -> None:
        if not _TASK_ID.fullmatch(task_id):
            raise ValueError("invalid task ID")

    def _current_dir(self, task_id: str) -> Path:
        self._validate_task_id(task_id)
        return self.tasks_root / task_id

    def _archived_dir(self, task_id: str) -> Path:
        self._validate_task_id(task_id)
        return self.trash_root / task_id

    def _record_path(self, directory: Path) -> Path:
        return directory / "task.yaml"

    def _atomic_write(self, directory: Path, record: TaskRecord) -> None:
        if directory.is_symlink():
            raise RuntimeError("task directory must not be a symlink")
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        evidence = directory / "evidence"
        evidence.mkdir(exist_ok=True, mode=0o750)
        path = self._record_path(directory)
        temporary = directory / f".task.{uuid4().hex}.tmp"
        payload = yaml.safe_dump(
            record.model_dump(),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
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

    def _read_dir(self, directory: Path) -> TaskRecord:
        if directory.is_symlink() or not directory.is_dir():
            raise RuntimeError(f"invalid task directory: {directory.name}")
        path = self._record_path(directory)
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"invalid task record: {directory.name}")
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            record = TaskRecord.model_validate(payload)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise RuntimeError(f"invalid task record: {directory.name}") from exc
        if record.id != directory.name:
            raise RuntimeError(f"task ID does not match directory: {directory.name}")
        return record

    def create(
        self,
        *,
        title: str,
        objective: str,
        project_ref: str | None = None,
        next_action: str | None = None,
        retained: bool = False,
    ) -> TaskRecord:
        now = self._now()
        record = TaskRecord(
            id=new_task_id(now),
            title=title,
            objective=objective,
            project_ref=project_ref,
            next_action=next_action,
            retained=retained,
            created_at=format_timestamp(now),
            updated_at=format_timestamp(now),
        )
        directory = self._current_dir(record.id)
        if directory.exists() or self._archived_dir(record.id).exists():
            raise RuntimeError(f"task already exists: {record.id}")
        directory.mkdir(mode=0o750)
        try:
            self._atomic_write(directory, record)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return record

    def get(self, task_id: str) -> TaskRecord:
        current = self._current_dir(task_id)
        archived = self._archived_dir(task_id)
        if current.is_dir() and archived.exists():
            raise RuntimeError(f"task exists in current and trash: {task_id}")
        if current.is_dir():
            return self._read_dir(current)
        if archived.is_dir():
            return self._read_dir(archived)
        raise ValueError(f"unknown task: {task_id}")

    def require_open(self, task_id: str) -> TaskRecord:
        current = self._current_dir(task_id)
        archived = self._archived_dir(task_id)
        if archived.exists():
            raise ValueError(f"task is archived: {task_id}")
        if not current.is_dir():
            raise ValueError(f"unknown task: {task_id}")
        record = self._read_dir(current)
        if record.status not in _OPEN_STATUSES:
            raise ValueError(f"task is terminal: {task_id}")
        return record

    def list(
        self,
        *,
        status: TaskStatus | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[TaskRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("task list limit must be between 1 and 500")
        records: list[TaskRecord] = []
        for directory in self.tasks_root.glob("task-*"):
            if directory.is_dir() and not directory.is_symlink():
                records.append(self._read_dir(directory))
        if include_archived:
            for directory in self.trash_root.glob("task-*"):
                if directory.is_dir() and not directory.is_symlink():
                    records.append(self._read_dir(directory))
        if status is not None:
            records = [record for record in records if record.status == status]
        records.sort(key=lambda record: (record.updated_at, record.id), reverse=True)
        return records[:limit]

    def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        objective: str | None = None,
        project_ref: str | None = None,
        clear_project_ref: bool = False,
        status: TaskStatus | None = None,
        next_action: str | None = None,
        clear_next_action: bool = False,
        retained: bool | None = None,
    ) -> TaskRecord:
        directory = self._current_dir(task_id)
        if self._archived_dir(task_id).exists():
            raise ValueError(f"task is archived: {task_id}")
        if not directory.is_dir():
            raise ValueError(f"unknown task: {task_id}")
        record = self._read_dir(directory)

        if record.status in _TERMINAL_STATUSES and status is not None and status != record.status:
            raise ValueError("terminal task status cannot be changed")
        if clear_project_ref and project_ref is not None:
            raise ValueError("project_ref and clear_project_ref are mutually exclusive")
        if clear_next_action and next_action is not None:
            raise ValueError("next_action and clear_next_action are mutually exclusive")

        updates: dict[str, object] = {"updated_at": format_timestamp(self._now())}
        if title is not None:
            updates["title"] = title
        if objective is not None:
            updates["objective"] = objective
        if project_ref is not None or clear_project_ref:
            updates["project_ref"] = None if clear_project_ref else project_ref
        if status is not None:
            updates["status"] = status
        if next_action is not None or clear_next_action:
            updates["next_action"] = None if clear_next_action else next_action
        if retained is not None:
            updates["retained"] = retained

        updated = TaskRecord.model_validate({**record.model_dump(), **updates})
        self._atomic_write(directory, updated)
        return updated

    def archive(self, task_id: str) -> TaskRecord:
        current = self._current_dir(task_id)
        archived = self._archived_dir(task_id)
        if archived.is_dir() and not current.exists():
            return self._read_dir(archived)
        if current.exists() and archived.exists():
            raise RuntimeError(f"task exists in current and trash: {task_id}")
        if not current.is_dir():
            raise ValueError(f"unknown task: {task_id}")

        record = self._read_dir(current)
        if record.status not in _TERMINAL_STATUSES:
            raise ValueError("only completed or cancelled tasks can be archived")
        if self.tasks_root.stat().st_dev != self.trash_root.stat().st_dev:
            raise RuntimeError("task and trash roots must be on the same filesystem")

        updated = record.model_copy(
            update={
                "updated_at": format_timestamp(self._now()),
                "archived_at": format_timestamp(self._now()),
            }
        )
        self._atomic_write(current, updated)

        os.rename(current, archived)
        return self._read_dir(archived)

    def cleanup(self) -> list[str]:
        if self.archived_days is None:
            return []
        cutoff = self._now() - timedelta(days=self.archived_days)
        removed: list[str] = []
        for directory in sorted(self.trash_root.glob("task-*")):
            if directory.is_symlink() or not directory.is_dir():
                continue
            record = self._read_dir(directory)
            if record.status not in _TERMINAL_STATUSES:
                continue
            if record.archived_at is None or record.retained:
                continue
            if parse_timestamp(record.archived_at) > cutoff:
                continue
            shutil.rmtree(directory)
            removed.append(record.id)
        return removed

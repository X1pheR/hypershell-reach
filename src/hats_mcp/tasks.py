from __future__ import annotations

import fcntl
import os
import re
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Callable, Iterator, Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

TaskStatus = Literal["active", "partial", "blocked", "completed", "cancelled"]
EvidenceClass = Literal["observed", "configured", "documented", "planned", "unknown"]
AssumptionImpact = Literal["low", "medium", "high"]
BoundedTaskText = Annotated[str, Field(min_length=1, max_length=1_000)]

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


class TaskSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: EvidenceClass
    reference: str = Field(min_length=1, max_length=512)
    purpose: str = Field(min_length=1, max_length=1_000)


class TaskAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=1_000)
    evidence_class: EvidenceClass
    impact_if_wrong: AssumptionImpact
    decision: str = Field(min_length=1, max_length=1_000)


class TaskContinuity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization: str | None = Field(default=None, min_length=1, max_length=2_000)
    sources: list[TaskSource] = Field(default_factory=list, max_length=20)
    completed: list[BoundedTaskText] = Field(default_factory=list, max_length=50)
    validation: list[BoundedTaskText] = Field(default_factory=list, max_length=50)
    cleanup: list[BoundedTaskText] = Field(default_factory=list, max_length=25)
    recovery: str | None = Field(default=None, min_length=1, max_length=2_000)
    blockers: list[BoundedTaskText] = Field(default_factory=list, max_length=25)
    assumptions: list[TaskAssumption] = Field(default_factory=list, max_length=20)


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2] = 2
    revision: int = Field(default=0, ge=0)
    id: str
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=4_000)
    project_ref: str | None = Field(default=None, min_length=1, max_length=256)
    status: TaskStatus = "active"
    next_action: str | None = Field(default=None, min_length=1, max_length=2_000)
    continuity: TaskContinuity = Field(default_factory=TaskContinuity)
    retained: bool = False
    created_at: str
    updated_at: str
    archived_at: str | None = None

    @model_validator(mode="after")
    def validate_schema_revision(self) -> "TaskRecord":
        if self.schema_version == 1 and self.revision != 0:
            raise ValueError("schema v1 task revision must be zero")
        if self.schema_version == 2 and self.revision < 1:
            raise ValueError("schema v2 task revision must be at least one")
        return self

    def summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "revision": self.revision,
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
        read_only: bool = False,
    ) -> None:
        self.tasks_root = Path(tasks_root)
        self.trash_root = Path(trash_root)
        self.lock_root = self.tasks_root / ".locks"
        self.archived_days = archived_days
        self._now = now
        self.read_only = read_only
        if not self.read_only:
            self.tasks_root.mkdir(parents=True, exist_ok=True, mode=0o750)
            self.trash_root.mkdir(parents=True, exist_ok=True, mode=0o750)
            self.lock_root.mkdir(exist_ok=True, mode=0o750)

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

    def _validate_directory_entry(self, directory: Path) -> None:
        if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
            raise RuntimeError(f"invalid task directory: {directory.name}")

    def _iter_task_directories(self, root: Path) -> Iterator[Path]:
        for directory in sorted(root.glob("task-*")):
            self._validate_directory_entry(directory)
            if directory.is_dir():
                yield directory

    def _require_writable(self) -> None:
        if self.read_only:
            raise RuntimeError("task store is read-only")

    @contextmanager
    def _lock(self, task_id: str) -> Iterator[None]:
        self._require_writable()
        self._validate_task_id(task_id)
        path = self.lock_root / f"{task_id}.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _fsync_directory(self, directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(directory, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _atomic_write(self, directory: Path, record: TaskRecord) -> None:
        self._require_writable()
        if directory.is_symlink():
            raise RuntimeError("task directory must not be a symlink")
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
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
            self._fsync_directory(directory)
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
        continuity: TaskContinuity | None = None,
        retained: bool = False,
    ) -> TaskRecord:
        self._require_writable()
        now = self._now()
        record = TaskRecord(
            schema_version=2,
            revision=1,
            id=new_task_id(now),
            title=title,
            objective=objective,
            project_ref=project_ref,
            next_action=next_action,
            continuity=continuity or TaskContinuity(),
            retained=retained,
            created_at=format_timestamp(now),
            updated_at=format_timestamp(now),
        )
        with self._lock(record.id):
            directory = self._current_dir(record.id)
            if directory.exists() or self._archived_dir(record.id).exists():
                raise RuntimeError(f"task already exists: {record.id}")
            directory.mkdir(mode=0o750)
            try:
                self._atomic_write(directory, record)
                self._fsync_directory(self.tasks_root)
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                self._fsync_directory(self.tasks_root)
                raise
        return record

    def get(self, task_id: str) -> TaskRecord:
        current = self._current_dir(task_id)
        archived = self._archived_dir(task_id)
        self._validate_directory_entry(current)
        self._validate_directory_entry(archived)
        if current.exists() and archived.exists():
            raise RuntimeError(f"task exists in current and archive: {task_id}")
        if current.is_dir():
            return self._read_dir(current)
        if archived.is_dir():
            return self._read_dir(archived)
        raise ValueError(f"unknown task: {task_id}")

    def require_open(self, task_id: str) -> TaskRecord:
        current = self._current_dir(task_id)
        archived = self._archived_dir(task_id)
        self._validate_directory_entry(current)
        self._validate_directory_entry(archived)
        if current.exists() and archived.exists():
            raise RuntimeError(f"task exists in current and archive: {task_id}")
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
        active_records: list[TaskRecord] = []
        archived_records: list[TaskRecord] = []
        seen: set[str] = set()
        for directory in self._iter_task_directories(self.tasks_root):
            record = self._read_dir(directory)
            if record.id in seen:
                raise RuntimeError(f"duplicate task ID: {record.id}")
            seen.add(record.id)
            active_records.append(record)
        for directory in self._iter_task_directories(self.trash_root):
            record = self._read_dir(directory)
            if record.id in seen:
                raise RuntimeError(f"duplicate task ID: {record.id}")
            seen.add(record.id)
            archived_records.append(record)
        records = active_records + (archived_records if include_archived else [])
        if status is not None:
            records = [record for record in records if record.status == status]
        records.sort(key=lambda record: (record.updated_at, record.id), reverse=True)
        return records[:limit]

    def _validate_edit_args(
        self,
        *,
        project_ref: str | None,
        clear_project_ref: bool,
        next_action: str | None,
        clear_next_action: bool,
    ) -> None:
        if clear_project_ref and project_ref is not None:
            raise ValueError("project_ref and clear_project_ref are mutually exclusive")
        if clear_next_action and next_action is not None:
            raise ValueError("next_action and clear_next_action are mutually exclusive")

    def _build_updated(
        self,
        record: TaskRecord,
        *,
        title: str | None = None,
        objective: str | None = None,
        project_ref: str | None = None,
        clear_project_ref: bool = False,
        status: TaskStatus | None = None,
        next_action: str | None = None,
        clear_next_action: bool = False,
        continuity: TaskContinuity | None = None,
        retained: bool | None = None,
        archived_at: str | None = None,
    ) -> TaskRecord:
        updates: dict[str, object] = {
            "schema_version": 2,
            "revision": record.revision + 1,
            "updated_at": format_timestamp(self._now()),
        }
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
        if continuity is not None:
            updates["continuity"] = continuity
        if retained is not None:
            updates["retained"] = retained
        if archived_at is not None:
            updates["archived_at"] = archived_at
        return TaskRecord.model_validate({**record.model_dump(), **updates})

    def _desired_close_matches(
        self,
        record: TaskRecord,
        *,
        status: Literal["completed", "cancelled"],
        title: str | None,
        objective: str | None,
        project_ref: str | None,
        clear_project_ref: bool,
        next_action: str | None,
        clear_next_action: bool,
        continuity: TaskContinuity | None,
        retained: bool | None,
    ) -> bool:
        if record.status != status:
            return False
        if title is not None and record.title != title:
            return False
        if objective is not None and record.objective != objective:
            return False
        if project_ref is not None and record.project_ref != project_ref:
            return False
        if clear_project_ref and record.project_ref is not None:
            return False
        if next_action is not None and record.next_action != next_action:
            return False
        if clear_next_action and record.next_action is not None:
            return False
        if continuity is not None and record.continuity != TaskContinuity.model_validate(continuity):
            return False
        if retained is not None and record.retained != retained:
            return False
        return True

    def update(
        self,
        task_id: str,
        *,
        expected_revision: int | None = None,
        title: str | None = None,
        objective: str | None = None,
        project_ref: str | None = None,
        clear_project_ref: bool = False,
        status: TaskStatus | None = None,
        next_action: str | None = None,
        clear_next_action: bool = False,
        continuity: TaskContinuity | None = None,
        retained: bool | None = None,
    ) -> TaskRecord:
        self._require_writable()
        self._validate_edit_args(
            project_ref=project_ref,
            clear_project_ref=clear_project_ref,
            next_action=next_action,
            clear_next_action=clear_next_action,
        )
        if status in _TERMINAL_STATUSES:
            return self.close(
                task_id,
                status=status,
                expected_revision=expected_revision,
                title=title,
                objective=objective,
                project_ref=project_ref,
                clear_project_ref=clear_project_ref,
                next_action=next_action,
                clear_next_action=clear_next_action,
                continuity=continuity,
                retained=retained,
            )
        with self._lock(task_id):
            directory = self._current_dir(task_id)
            archived = self._archived_dir(task_id)
            self._validate_directory_entry(directory)
            self._validate_directory_entry(archived)
            if directory.exists() and archived.exists():
                raise RuntimeError(f"task exists in current and archive: {task_id}")
            if archived.exists():
                archived_record = self._read_dir(archived)
                if status is not None and status != archived_record.status:
                    raise ValueError("terminal task status cannot be changed")
                raise ValueError(f"task is archived: {task_id}")
            if not directory.is_dir():
                raise ValueError(f"unknown task: {task_id}")
            record = self._read_dir(directory)
            if record.status in _TERMINAL_STATUSES:
                if status is not None and status != record.status:
                    raise ValueError("terminal task status cannot be changed")
                raise ValueError(f"task is terminal: {task_id}")
            if expected_revision is not None and record.revision != expected_revision:
                raise ValueError(
                    f"stale task revision: expected {expected_revision}, current {record.revision}"
                )
            updated = self._build_updated(
                record,
                title=title,
                objective=objective,
                project_ref=project_ref,
                clear_project_ref=clear_project_ref,
                status=status,
                next_action=next_action,
                clear_next_action=clear_next_action,
                continuity=continuity,
                retained=retained,
            )
            self._atomic_write(directory, updated)
            return updated

    def _move_to_archive(self, current: Path, archived: Path) -> None:
        if current.exists() and archived.exists():
            raise RuntimeError(f"task exists in current and archive: {current.name}")
        if self.tasks_root.stat().st_dev != self.trash_root.stat().st_dev:
            raise RuntimeError("task current and archive roots must be on the same filesystem")
        os.replace(current, archived)
        self._fsync_directory(self.tasks_root)
        self._fsync_directory(self.trash_root)

    def close(
        self,
        task_id: str,
        *,
        status: Literal["completed", "cancelled"],
        expected_revision: int | None = None,
        title: str | None = None,
        objective: str | None = None,
        project_ref: str | None = None,
        clear_project_ref: bool = False,
        next_action: str | None = None,
        clear_next_action: bool = False,
        continuity: TaskContinuity | None = None,
        retained: bool | None = None,
    ) -> TaskRecord:
        self._require_writable()
        self._validate_edit_args(
            project_ref=project_ref,
            clear_project_ref=clear_project_ref,
            next_action=next_action,
            clear_next_action=clear_next_action,
        )
        with self._lock(task_id):
            current = self._current_dir(task_id)
            archived = self._archived_dir(task_id)
            self._validate_directory_entry(current)
            self._validate_directory_entry(archived)
            if current.exists() and archived.exists():
                raise RuntimeError(f"task exists in current and archive: {task_id}")
            if archived.is_dir():
                record = self._read_dir(archived)
                if self._desired_close_matches(
                    record,
                    status=status,
                    title=title,
                    objective=objective,
                    project_ref=project_ref,
                    clear_project_ref=clear_project_ref,
                    next_action=next_action,
                    clear_next_action=clear_next_action,
                    continuity=continuity,
                    retained=retained,
                ):
                    self._fsync_directory(self.tasks_root)
                    self._fsync_directory(self.trash_root)
                    return record
                if record.status != status:
                    raise ValueError("terminal task status cannot be changed")
                raise ValueError(f"task is already archived with different final state: {task_id}")
            if not current.is_dir():
                raise ValueError(f"unknown task: {task_id}")
            record = self._read_dir(current)
            if record.status in _TERMINAL_STATUSES and record.status != status:
                raise ValueError("terminal task status cannot be changed")
            if record.status in _TERMINAL_STATUSES and record.archived_at is not None:
                if not self._desired_close_matches(
                    record,
                    status=status,
                    title=title,
                    objective=objective,
                    project_ref=project_ref,
                    clear_project_ref=clear_project_ref,
                    next_action=next_action,
                    clear_next_action=clear_next_action,
                    continuity=continuity,
                    retained=retained,
                ):
                    raise ValueError(f"task has committed terminal state with different final data: {task_id}")
                self._move_to_archive(current, archived)
                return self._read_dir(archived)
            if expected_revision is not None and record.revision != expected_revision:
                raise ValueError(
                    f"stale task revision: expected {expected_revision}, current {record.revision}"
                )
            archived_at = format_timestamp(self._now())
            updated = self._build_updated(
                record,
                title=title,
                objective=objective,
                project_ref=project_ref,
                clear_project_ref=clear_project_ref,
                status=status,
                next_action=next_action,
                clear_next_action=clear_next_action,
                continuity=continuity,
                retained=retained,
                archived_at=archived_at,
            )
            self._atomic_write(current, updated)
            self._move_to_archive(current, archived)
            return self._read_dir(archived)

    def archive(self, task_id: str) -> TaskRecord:
        self._require_writable()
        record = self.get(task_id)
        if record.status not in _TERMINAL_STATUSES:
            raise ValueError("only completed or cancelled tasks can be archived")
        return self.close(task_id, status=record.status)

    def repair(self) -> list[str]:
        self._require_writable()
        repaired: list[str] = []
        for directory in self._iter_task_directories(self.tasks_root):
            task_id = directory.name
            archived = self._archived_dir(task_id)
            if archived.exists():
                raise RuntimeError(f"task exists in current and archive: {task_id}")
            try:
                record = self._read_dir(directory)
            except RuntimeError:
                if not directory.exists() and archived.is_dir():
                    continue
                raise
            if record.status not in _TERMINAL_STATUSES:
                continue
            self.close(task_id, status=record.status)
            repaired.append(task_id)
        return repaired

    def cleanup(self) -> list[str]:
        self._require_writable()
        if self.archived_days is None:
            return []
        cutoff = self._now() - timedelta(days=self.archived_days)
        removed: list[str] = []
        for directory in self._iter_task_directories(self.trash_root):
            record = self._read_dir(directory)
            if record.status not in _TERMINAL_STATUSES:
                continue
            if record.archived_at is None or record.retained:
                continue
            if parse_timestamp(record.archived_at) > cutoff:
                continue
            shutil.rmtree(directory)
            removed.append(record.id)
        if removed:
            self._fsync_directory(self.trash_root)
        return removed

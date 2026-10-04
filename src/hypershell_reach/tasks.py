from __future__ import annotations

import fcntl
import json
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

from .database import ReachDatabase, database_for, timestamp

TaskStatus = Literal["active", "partial", "blocked", "completed", "cancelled"]
EvidenceClass = Literal["observed", "configured", "documented", "planned", "unknown"]
AssumptionImpact = Literal["low", "medium", "high"]
BoundedTaskText = Annotated[str, Field(min_length=1, max_length=1_000)]

_TASK_ID = re.compile(r"^task-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")
_OPEN_STATUSES = {"active", "partial", "blocked"}
_TERMINAL_STATUSES = {"completed", "cancelled"}
PENDING_MUTATION_BLOCKER_PREFIX = "[reach:pending-mutation]"
_TASK_LEASE_ID = re.compile(r"^tlease-[0-9a-f]{32}$")
_TASK_LEASE_MIN_SECONDS = 30
_TASK_LEASE_MAX_SECONDS = 3600


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


class TaskExecutionLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    lease_id: str = Field(pattern=r"^tlease-[0-9a-f]{32}$")
    executor_id: str = Field(min_length=1, max_length=256)
    scope: str = Field(min_length=1, max_length=512)
    acquired_at: str
    refreshed_at: str
    expires_at: str


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
        database: ReachDatabase | str | Path | None = None,
    ) -> None:
        # In unified mode these paths identify legacy layout only; all state access
        # is dispatched to the database and no directory is created or written.
        self.tasks_root = Path(tasks_root)
        self.trash_root = Path(trash_root)
        self.lock_root = self.tasks_root / ".locks"
        self.archived_days = archived_days
        self._now = now
        self.read_only = read_only
        self.database = database_for(database, read_only=read_only) if database is not None else None
        if self.database is not None:
            self.database.validate()
        if not self.read_only and self.database is None:
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

    def _lease_path(self, directory: Path) -> Path:
        return directory / "execution-lease.yaml"

    def _validate_directory_entry(self, directory: Path) -> None:
        if self.database is not None:
            return
        if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
            raise RuntimeError(f"invalid task directory: {directory.name}")

    def _iter_task_directories(self, root: Path) -> Iterator[Path]:
        if self.database is not None:
            with self.database.connection() as conn:
                rows = conn.execute("SELECT id FROM tasks WHERE archived=? ORDER BY id",
                                    (int(root == self.trash_root),)).fetchall()
            for row in rows:
                yield root / row["id"]
            return
        for directory in sorted(root.glob("task-*")):
            self._validate_directory_entry(directory)
            if self._directory_exists(directory):
                yield directory

    def _directory_exists(self, directory: Path) -> bool:
        if self.database is None:
            return directory.is_dir()
        with self.database.connection() as conn:
            return conn.execute("SELECT 1 FROM tasks WHERE id=? AND archived=?",
                                (directory.name, int(directory.parent == self.trash_root))).fetchone() is not None

    def _remove_execution_lease(self, directory: Path) -> None:
        if self.database is not None:
            with self.database.connection(write=True) as conn:
                conn.execute("DELETE FROM task_leases WHERE task_id=?", (directory.name,))
        else:
            self._lease_path(directory).unlink(missing_ok=True)
            self._fsync_directory(directory)

    def _require_writable(self) -> None:
        if self.read_only:
            raise RuntimeError("task store is read-only")

    @contextmanager
    def _lock(self, task_id: str) -> Iterator[None]:
        self._require_writable()
        self._validate_task_id(task_id)
        if self.database is not None:
            with self.database.connection(write=True):
                yield
            return
        path = self.lock_root / f"{task_id}.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @contextmanager
    def _create_lock(self) -> Iterator[None]:
        self._require_writable()
        if self.database is not None:
            with self.database.connection(write=True):
                yield
            return
        path = self.lock_root / "create.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _continuity_identity(
        title: str,
        objective: str,
        project_ref: str | None,
    ) -> tuple[str, str, str | None]:
        return (
            title.strip(),
            objective.strip(),
            project_ref.strip() if project_ref is not None else None,
        )

    def _find_equivalent_open_task(
        self,
        *,
        title: str,
        objective: str,
        project_ref: str | None,
    ) -> TaskRecord | None:
        identity = self._continuity_identity(title, objective, project_ref)
        if self.database is not None:
            with self.database.connection() as conn:
                row = conn.execute(
                    "SELECT id,payload FROM tasks WHERE identity=? AND archived=0 AND status IN ('active','partial','blocked') ORDER BY id LIMIT 1",
                    (json.dumps(identity),),
                ).fetchone()
            if row is None:
                return None
            record = self._decode(row["payload"])
            if (record.id != row["id"] or record.status not in _OPEN_STATUSES
                    or record.archived_at is not None
                    or self._continuity_identity(record.title, record.objective, record.project_ref) != identity):
                raise RuntimeError("task identity does not match database index")
            return record
        for directory in self._iter_task_directories(self.tasks_root):
            record = self._read_dir(directory)
            if record.status not in _OPEN_STATUSES:
                continue
            if self._continuity_identity(record.title, record.objective, record.project_ref) == identity:
                return record
        return None

    def _fsync_directory(self, directory: Path) -> None:
        if self.database is not None:
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(directory, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _decode(payload: str) -> TaskRecord:
        try:
            record = TaskRecord.model_validate_json(payload)
            if not _TASK_ID.fullmatch(record.id):
                raise ValueError("invalid task ID")
            timestamp(record.created_at)
            timestamp(record.updated_at)
            if record.archived_at is not None:
                timestamp(record.archived_at)
            return record
        except (ValueError, TypeError) as exc:
            raise RuntimeError("invalid task record in database") from exc

    @staticmethod
    def _put(conn, record: TaskRecord, *, archived: bool) -> None:
        # Called only inside a write transaction. Full contract payload and typed
        # read columns are committed together, never separate state authorities.
        record = TaskStore._decode(record.model_dump_json())
        conn.execute(
            """INSERT INTO tasks (id,status,revision,archived,created_at,updated_at,
               archived_at,project_ref,retained,blocked,identity,payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status,revision=excluded.revision,
               archived=excluded.archived,created_at=excluded.created_at,updated_at=excluded.updated_at,
               archived_at=excluded.archived_at,project_ref=excluded.project_ref,retained=excluded.retained,
               blocked=excluded.blocked,identity=excluded.identity,payload=excluded.payload""",
            (record.id,record.status,record.revision,int(archived),timestamp(record.created_at),
             timestamp(record.updated_at),timestamp(record.archived_at) if record.archived_at else None,
             record.project_ref,int(record.retained),int(bool(record.continuity.blockers)),
             json.dumps(TaskStore._continuity_identity(record.title,record.objective,record.project_ref)),
             record.model_dump_json()),
        )

    def _atomic_write(self, directory: Path, record: TaskRecord) -> None:
        self._require_writable()
        if self.database is not None:
            with self.database.connection(write=True) as conn:
                self._put(conn, record, archived=directory.parent == self.trash_root)
            return
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

    def _atomic_write_execution_lease(
        self,
        directory: Path,
        lease: TaskExecutionLease,
    ) -> None:
        self._require_writable()
        if self.database is not None:
            with self.database.connection(write=True) as conn:
                conn.execute(
                    """INSERT INTO task_leases(task_id,executor_id,expires_at,payload) VALUES(?,?,?,?)
                       ON CONFLICT(task_id) DO UPDATE SET executor_id=excluded.executor_id,
                       expires_at=excluded.expires_at,payload=excluded.payload""",
                    (directory.name,lease.executor_id,timestamp(lease.expires_at),lease.model_dump_json()),
                )
            return
        path = self._lease_path(directory)
        temporary = directory / f".execution-lease.{uuid4().hex}.tmp"
        payload = yaml.safe_dump(
            lease.model_dump(),
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

    def _read_execution_lease(self, directory: Path) -> TaskExecutionLease | None:
        if self.database is not None:
            with self.database.connection() as conn:
                row = conn.execute("SELECT payload FROM task_leases WHERE task_id=?", (directory.name,)).fetchone()
            if row is None:
                return None
            try:
                lease = TaskExecutionLease.model_validate_json(row["payload"])
                for value in (lease.acquired_at, lease.refreshed_at, lease.expires_at):
                    timestamp(value)
                return lease
            except (ValueError, TypeError) as exc:
                raise RuntimeError(f"invalid task execution lease: {directory.name}") from exc
        path = self._lease_path(directory)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"invalid task execution lease: {directory.name}")
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            return TaskExecutionLease.model_validate(payload)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise RuntimeError(f"invalid task execution lease: {directory.name}") from exc

    def _active_execution_lease(self, directory: Path) -> TaskExecutionLease | None:
        lease = self._read_execution_lease(directory)
        if lease is None:
            return None
        if parse_timestamp(lease.expires_at) > self._now():
            return lease
        self._remove_execution_lease(directory)
        return None

    def _require_execution_lease(
        self,
        directory: Path,
        task_lease_id: str | None,
    ) -> TaskExecutionLease | None:
        lease = self._active_execution_lease(directory)
        if lease is None:
            return None
        if task_lease_id != lease.lease_id:
            raise ValueError(
                "active task execution lease requires matching task_lease_id"
            )
        return lease

    def acquire_execution_lease(
        self,
        task_id: str,
        *,
        executor_id: str,
        scope: str,
        lease_seconds: int,
    ) -> dict[str, object]:
        self._require_writable()
        if not executor_id or len(executor_id) > 256:
            raise ValueError("executor_id length is invalid")
        if not scope or len(scope) > 512:
            raise ValueError("scope length is invalid")
        if not _TASK_LEASE_MIN_SECONDS <= lease_seconds <= _TASK_LEASE_MAX_SECONDS:
            raise ValueError(
                f"lease_seconds must be between {_TASK_LEASE_MIN_SECONDS} and {_TASK_LEASE_MAX_SECONDS}"
            )
        with self._lock(task_id):
            directory = self._current_dir(task_id)
            archived = self._archived_dir(task_id)
            self._validate_directory_entry(directory)
            self._validate_directory_entry(archived)
            if self._directory_exists(archived):
                raise ValueError(f"task is archived: {task_id}")
            if not self._directory_exists(directory):
                raise ValueError(f"unknown task: {task_id}")
            record = self._read_dir(directory)
            if record.status not in _OPEN_STATUSES:
                raise ValueError(f"task is terminal: {task_id}")
            current = self._active_execution_lease(directory)
            if current is not None:
                same_owner = current.executor_id == executor_id and current.scope == scope
                return {
                    "acquired": same_owner,
                    "lease_id": current.lease_id if same_owner else None,
                    "owner_state": current.model_dump(),
                    "handoff_state": "owned" if same_owner else "busy",
                }
            now = self._now()
            lease = TaskExecutionLease(
                lease_id=f"tlease-{uuid4().hex}",
                executor_id=executor_id,
                scope=scope,
                acquired_at=format_timestamp(now),
                refreshed_at=format_timestamp(now),
                expires_at=format_timestamp(now + timedelta(seconds=lease_seconds)),
            )
            self._atomic_write_execution_lease(directory, lease)
            return {
                "acquired": True,
                "lease_id": lease.lease_id,
                "owner_state": lease.model_dump(),
                "handoff_state": "acquired",
            }

    def refresh_execution_lease(
        self,
        task_id: str,
        *,
        lease_id: str,
        lease_seconds: int,
    ) -> dict[str, object]:
        self._require_writable()
        if not _TASK_LEASE_ID.fullmatch(lease_id):
            raise ValueError("invalid task execution lease ID")
        if not _TASK_LEASE_MIN_SECONDS <= lease_seconds <= _TASK_LEASE_MAX_SECONDS:
            raise ValueError(
                f"lease_seconds must be between {_TASK_LEASE_MIN_SECONDS} and {_TASK_LEASE_MAX_SECONDS}"
            )
        with self._lock(task_id):
            directory = self._current_dir(task_id)
            record = self.require_open(task_id)
            if record.id != task_id:
                raise RuntimeError("task identity mismatch")
            current = self._active_execution_lease(directory)
            if current is None:
                raise ValueError("task execution lease is not active")
            if current.lease_id != lease_id:
                raise ValueError("task execution lease does not match")
            now = self._now()
            refreshed = current.model_copy(
                update={
                    "refreshed_at": format_timestamp(now),
                    "expires_at": format_timestamp(now + timedelta(seconds=lease_seconds)),
                }
            )
            self._atomic_write_execution_lease(directory, refreshed)
            return {
                "acquired": True,
                "lease_id": refreshed.lease_id,
                "owner_state": refreshed.model_dump(),
                "handoff_state": "refreshed",
            }

    def release_execution_lease(
        self,
        task_id: str,
        *,
        lease_id: str,
    ) -> dict[str, object]:
        self._require_writable()
        if not _TASK_LEASE_ID.fullmatch(lease_id):
            raise ValueError("invalid task execution lease ID")
        with self._lock(task_id):
            directory = self._current_dir(task_id)
            record = self.require_open(task_id)
            if record.id != task_id:
                raise RuntimeError("task identity mismatch")
            current = self._active_execution_lease(directory)
            if current is None:
                return {
                    "acquired": False,
                    "lease_id": None,
                    "owner_state": None,
                    "handoff_state": "released",
                }
            if current.lease_id != lease_id:
                raise ValueError("task execution lease does not match")
            self._remove_execution_lease(directory)
            self._fsync_directory(directory)
            return {
                "acquired": False,
                "lease_id": None,
                "owner_state": current.model_dump(),
                "handoff_state": "released",
            }

    def authorize_execution_lease(
        self,
        task_id: str,
        *,
        task_lease_id: str | None,
    ) -> TaskExecutionLease | None:
        self._require_writable()
        with self._lock(task_id):
            directory = self._current_dir(task_id)
            archived = self._archived_dir(task_id)
            self._validate_directory_entry(directory)
            self._validate_directory_entry(archived)
            if self._directory_exists(archived):
                raise ValueError(f"task is archived: {task_id}")
            if not self._directory_exists(directory):
                raise ValueError(f"unknown task: {task_id}")
            record = self._read_dir(directory)
            if record.status not in _OPEN_STATUSES:
                raise ValueError(f"task is terminal: {task_id}")
            return self._require_execution_lease(directory, task_lease_id)

    def _read_dir(self, directory: Path) -> TaskRecord:
        if self.database is not None:
            with self.database.connection() as conn:
                row = conn.execute("SELECT payload FROM tasks WHERE id=? AND archived=?",
                                   (directory.name,int(directory.parent == self.trash_root))).fetchone()
            if row is None:
                raise RuntimeError(f"invalid task record: {directory.name}")
            record = self._decode(row["payload"])
            if record.id != directory.name:
                raise RuntimeError("task ID does not match database key")
            return record
        if directory.is_symlink() or not self._directory_exists(directory):
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
        with self._create_lock():
            existing = self._find_equivalent_open_task(
                title=title,
                objective=objective,
                project_ref=project_ref,
            )
            if existing is not None:
                return existing

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
                if self._directory_exists(directory) or self._directory_exists(self._archived_dir(record.id)):
                    raise RuntimeError(f"task already exists: {record.id}")
                if self.database is None:
                    directory.mkdir(mode=0o750)
                try:
                    self._atomic_write(directory, record)
                    self._fsync_directory(self.tasks_root)
                except Exception:
                    if self.database is None:
                        shutil.rmtree(directory, ignore_errors=True)
                    self._fsync_directory(self.tasks_root)
                    raise
            return record

    def get(self, task_id: str) -> TaskRecord:
        if self.database is not None:
            self._validate_task_id(task_id)
            with self.database.connection() as conn:
                row = conn.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown task: {task_id}")
            record = self._decode(row["payload"])
            if record.id != task_id:
                raise RuntimeError("task ID does not match database key")
            return record
        current = self._current_dir(task_id)
        archived = self._archived_dir(task_id)
        self._validate_directory_entry(current)
        self._validate_directory_entry(archived)
        if self._directory_exists(current) and self._directory_exists(archived):
            raise RuntimeError(f"task exists in current and archive: {task_id}")
        if self._directory_exists(current):
            return self._read_dir(current)
        if self._directory_exists(archived):
            return self._read_dir(archived)
        raise ValueError(f"unknown task: {task_id}")

    def require_open(self, task_id: str) -> TaskRecord:
        if self.database is not None:
            self._validate_task_id(task_id)
            with self.database.connection() as conn:
                row = conn.execute("SELECT archived,payload FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown task: {task_id}")
            record = self._decode(row["payload"])
            if record.id != task_id:
                raise RuntimeError("task ID does not match database key")
            if row["archived"]:
                raise ValueError(f"task is archived: {task_id}")
            if record.status not in _OPEN_STATUSES:
                raise ValueError(f"task is terminal: {task_id}")
            return record
        current = self._current_dir(task_id)
        archived = self._archived_dir(task_id)
        self._validate_directory_entry(current)
        self._validate_directory_entry(archived)
        if self._directory_exists(current) and self._directory_exists(archived):
            raise RuntimeError(f"task exists in current and archive: {task_id}")
        if self._directory_exists(archived):
            raise ValueError(f"task is archived: {task_id}")
        if not self._directory_exists(current):
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
        if self.database is not None:
            return self.query(status=status, archived=None if include_archived else False, limit=limit)
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

    @staticmethod
    def _merge_continuity(
        current: TaskContinuity,
        patch: TaskContinuity | dict[str, object],
    ) -> TaskContinuity:
        parsed = patch if isinstance(patch, TaskContinuity) else TaskContinuity.model_validate(patch)
        updates = {name: getattr(parsed, name) for name in parsed.model_fields_set}
        return TaskContinuity.model_validate({**current.model_dump(), **updates})

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
        if continuity is not None and record.continuity != self._merge_continuity(
            record.continuity, continuity
        ):
            return False
        if retained is not None and record.retained != retained:
            return False
        return True

    def mark_mutation_pending(
        self,
        task_id: str,
        *,
        purpose: str,
        task_lease_id: str | None = None,
    ) -> TaskRecord:
        self._require_writable()
        with self._lock(task_id):
            directory = self._current_dir(task_id)
            archived = self._archived_dir(task_id)
            self._validate_directory_entry(directory)
            self._validate_directory_entry(archived)
            if self._directory_exists(directory) and self._directory_exists(archived):
                raise RuntimeError(f"task exists in current and archive: {task_id}")
            if self._directory_exists(archived):
                raise ValueError(f"task is archived: {task_id}")
            if not self._directory_exists(directory):
                raise ValueError(f"unknown task: {task_id}")
            record = self._read_dir(directory)
            self._require_execution_lease(directory, task_lease_id)
            if record.status not in _OPEN_STATUSES:
                raise ValueError(f"task is terminal: {task_id}")
            blockers = [
                blocker
                for blocker in record.continuity.blockers
                if not blocker.startswith(PENDING_MUTATION_BLOCKER_PREFIX)
            ]
            blockers.append(
                f"{PENDING_MUTATION_BLOCKER_PREFIX} Mutating execution requires "
                f"postcondition reconciliation before Task completion. Latest purpose: {purpose}"
            )
            continuity = record.continuity.model_copy(update={"blockers": blockers})
            updated = self._build_updated(record, continuity=continuity)
            self._atomic_write(directory, updated)
            return updated

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
        reconcile_mutation: str | None = None,
        retained: bool | None = None,
        task_lease_id: str | None = None,
    ) -> TaskRecord:
        self._require_writable()
        self._validate_edit_args(
            project_ref=project_ref,
            clear_project_ref=clear_project_ref,
            next_action=next_action,
            clear_next_action=clear_next_action,
        )
        if status in _TERMINAL_STATUSES:
            if reconcile_mutation is not None:
                raise ValueError("reconcile mutation before terminal task close")
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
                task_lease_id=task_lease_id,
            )
        with self._lock(task_id):
            directory = self._current_dir(task_id)
            archived = self._archived_dir(task_id)
            self._validate_directory_entry(directory)
            self._validate_directory_entry(archived)
            if self._directory_exists(directory) and self._directory_exists(archived):
                raise RuntimeError(f"task exists in current and archive: {task_id}")
            if self._directory_exists(archived):
                archived_record = self._read_dir(archived)
                if status is not None and status != archived_record.status:
                    raise ValueError("terminal task status cannot be changed")
                raise ValueError(f"task is archived: {task_id}")
            if not self._directory_exists(directory):
                raise ValueError(f"unknown task: {task_id}")
            record = self._read_dir(directory)
            self._require_execution_lease(directory, task_lease_id)
            if record.status in _TERMINAL_STATUSES:
                if status is not None and status != record.status:
                    raise ValueError("terminal task status cannot be changed")
                raise ValueError(f"task is terminal: {task_id}")
            if expected_revision is not None and record.revision != expected_revision:
                raise ValueError(
                    f"stale task revision: expected {expected_revision}, current {record.revision}"
                )
            merged_continuity = (
                self._merge_continuity(record.continuity, continuity)
                if continuity is not None
                else record.continuity
            )
            had_pending_mutation = any(
                blocker.startswith(PENDING_MUTATION_BLOCKER_PREFIX)
                for blocker in record.continuity.blockers
            )
            has_pending_after_patch = any(
                blocker.startswith(PENDING_MUTATION_BLOCKER_PREFIX)
                for blocker in merged_continuity.blockers
            )
            if had_pending_mutation and not has_pending_after_patch and reconcile_mutation is None:
                raise ValueError(
                    "pending mutation blocker requires explicit reconcile_mutation evidence"
                )
            if reconcile_mutation is not None:
                if not had_pending_mutation:
                    raise ValueError("task has no pending mutation to reconcile")
                blockers = [
                    blocker
                    for blocker in merged_continuity.blockers
                    if not blocker.startswith(PENDING_MUTATION_BLOCKER_PREFIX)
                ]
                validation = [
                    *merged_continuity.validation,
                    f"Mutation reconciliation: {reconcile_mutation}",
                ]
                merged_continuity = TaskContinuity.model_validate(
                    {**merged_continuity.model_dump(), "blockers": blockers, "validation": validation}
                )
            continuity_update = (
                merged_continuity
                if continuity is not None or reconcile_mutation is not None
                else None
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
                continuity=continuity_update,
                retained=retained,
            )
            self._atomic_write(directory, updated)
            return updated

    def _move_to_archive(self, current: Path, archived: Path) -> None:
        if self.database is not None:
            with self.database.connection(write=True) as conn:
                conn.execute("UPDATE tasks SET archived=1 WHERE id=? AND archived=0", (current.name,))
            return
        if self._directory_exists(current) and self._directory_exists(archived):
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
        task_lease_id: str | None = None,
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
            if self._directory_exists(current) and self._directory_exists(archived):
                raise RuntimeError(f"task exists in current and archive: {task_id}")
            if self._directory_exists(archived):
                record = self._read_dir(archived)
                if any(blocker.startswith(PENDING_MUTATION_BLOCKER_PREFIX) for blocker in record.continuity.blockers):
                    raise ValueError("pending mutation requires explicit reconciliation before terminal task close")
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
                    self._remove_execution_lease(archived)
                    self._fsync_directory(archived)
                    self._fsync_directory(self.tasks_root)
                    self._fsync_directory(self.trash_root)
                    return record
                if record.status != status:
                    raise ValueError("terminal task status cannot be changed")
                raise ValueError(f"task is already archived with different final state: {task_id}")
            if not self._directory_exists(current):
                raise ValueError(f"unknown task: {task_id}")
            record = self._read_dir(current)
            self._require_execution_lease(current, task_lease_id)
            if any(blocker.startswith(PENDING_MUTATION_BLOCKER_PREFIX) for blocker in record.continuity.blockers):
                raise ValueError("pending mutation requires explicit reconciliation before terminal task close")
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
                self._remove_execution_lease(archived)
                self._fsync_directory(archived)
                return self._read_dir(archived)
            if expected_revision is not None and record.revision != expected_revision:
                raise ValueError(
                    f"stale task revision: expected {expected_revision}, current {record.revision}"
                )
            archived_at = format_timestamp(self._now())
            merged_continuity = (
                self._merge_continuity(record.continuity, continuity)
                if continuity is not None
                else None
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
                continuity=merged_continuity,
                retained=retained,
                archived_at=archived_at,
            )
            if any(blocker.startswith(PENDING_MUTATION_BLOCKER_PREFIX) for blocker in updated.continuity.blockers):
                raise ValueError("pending mutation requires explicit reconciliation before terminal task close")
            if status == "completed" and updated.next_action is not None:
                raise ValueError("completed task cannot retain next_action")
            if status == "completed" and updated.continuity.blockers:
                raise ValueError("completed task cannot retain blockers")
            self._atomic_write(current, updated)
            self._move_to_archive(current, archived)
            self._remove_execution_lease(archived)
            self._fsync_directory(archived)
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
        if self.database is not None:
            with self.database.connection() as conn:
                rows = conn.execute("SELECT id,payload FROM tasks WHERE archived=0 ORDER BY id").fetchall()
            for row in rows:
                record = self._decode(row["payload"])
                if record.id != row["id"]:
                    raise RuntimeError("task ID does not match database key")
                if record.status in _TERMINAL_STATUSES:
                    self.close(record.id, status=record.status)
                    repaired.append(record.id)
            return repaired
        for directory in self._iter_task_directories(self.tasks_root):
            task_id = directory.name
            archived = self._archived_dir(task_id)
            if self._directory_exists(archived):
                raise RuntimeError(f"task exists in current and archive: {task_id}")
            try:
                record = self._read_dir(directory)
            except RuntimeError:
                if not self._directory_exists(directory) and self._directory_exists(archived):
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
        if self.database is not None:
            with self.database.connection(write=True) as conn:
                rows = conn.execute(
                    "SELECT id,status,retained,archived_at,payload FROM tasks WHERE archived=1 AND status IN ('completed','cancelled') AND retained=0 AND archived_at<=? ORDER BY id",
                    (cutoff.timestamp(),),
                ).fetchall()
                for row in rows:
                    record = self._decode(row["payload"])
                    if (record.id != row["id"] or record.status != row["status"]
                            or int(record.retained) != row["retained"]
                            or record.archived_at is None
                            or timestamp(record.archived_at) != row["archived_at"]):
                        raise RuntimeError("task cleanup index disagrees with payload")
                ids = [row["id"] for row in rows]
                conn.executemany("DELETE FROM tasks WHERE id=?", ((task_id,) for task_id in ids))
            return ids
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

    @staticmethod
    def _query_filter(*, status=None, archived=False, project_ref=None, retained=None, blocked=None, q=None):
        where, args = [], []
        for column, value in (("status", status), ("archived", archived), ("project_ref", project_ref),
                              ("retained", retained), ("blocked", blocked)):
            if value is not None:
                where.append(f"{column}=?")
                args.append(int(value) if isinstance(value, bool) else value)
        if q:
            term = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            fields = ("id", "status", "project_ref", "json_extract(payload,'$.title')",
                      "json_extract(payload,'$.next_action')")
            where.append("(" + " OR ".join(f"{f} LIKE ? ESCAPE '\\'" for f in fields) + ")")
            args.extend([term] * len(fields))
        return (" WHERE " + " AND ".join(where) if where else ""), args

    def query(self, *, status=None, archived=False, project_ref=None, retained=None, blocked=None,
              q=None, sort="updated_at", descending=True, limit=100, offset=0) -> list[TaskRecord]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("task query limit must be between 1 and 500 and offset nonnegative")
        columns = {"updated_at":"updated_at","created_at":"created_at","archived_at":"archived_at",
                   "id":"id","status":"status","project_ref":"project_ref",
                   "title":"json_extract(payload,'$.title')","next_action":"json_extract(payload,'$.next_action')"}
        if sort not in columns:
            raise ValueError("invalid task query sort")
        if self.database is None:
            entries = [(self._read_dir(d), root == self.trash_root)
                       for root in (self.tasks_root,self.trash_root)
                       for d in self._iter_task_directories(root)]
            if len({r.id for r, _ in entries}) != len(entries):
                raise RuntimeError("duplicate task ID in legacy store")
            records = [r for r, physical_archive in entries if (status is None or r.status == status)
                       and (archived is None or physical_archive == archived)
                       and (project_ref is None or r.project_ref == project_ref)
                       and (retained is None or r.retained == retained)
                       and (blocked is None or bool(r.continuity.blockers) == blocked)
                       and (not q or q.casefold() in " ".join(str(x or "") for x in
                            (r.id,r.title,r.status,r.project_ref,r.next_action)).casefold())]
            records.sort(key=lambda r:(getattr(r,sort) or "",r.id),reverse=descending)
            return records[offset:offset+limit]
        where,args = self._query_filter(status=status,archived=archived,project_ref=project_ref,
                                        retained=retained,blocked=blocked,q=q)
        direction = "DESC" if descending else "ASC"
        with self.database.connection() as conn:
            rows = conn.execute(f"SELECT id,payload FROM tasks{where} ORDER BY {columns[sort]} {direction},id {direction} LIMIT ? OFFSET ?",
                                (*args,limit,offset)).fetchall()
        records = [self._decode(row["payload"]) for row in rows]
        if any(r.id != row["id"] for r,row in zip(records,rows)):
            raise RuntimeError("task ID does not match database key")
        return records

    def query_summaries(self, **filters) -> list[dict[str, object]]:
        """Project physical archive state without changing historical payloads.

        The bounded batch shares a read snapshot with the page query; it does
        not issue one lookup per Task or invent missing archive timestamps.
        """
        if self.database is None:
            return [{**record.summary(), "archived": self._archived_dir(record.id).is_dir()}
                    for record in self.query(**filters)]
        with self.database.connection() as conn:
            records = self.query(**filters)
            if not records:
                return []
            placeholders = ",".join("?" for _ in records)
            archived = dict(conn.execute(f"SELECT id,archived FROM tasks WHERE id IN ({placeholders})",
                                         [record.id for record in records]))
            return [{**record.summary(), "archived": bool(archived[record.id])} for record in records]

    def count(self, *, status=None, archived=False, project_ref=None, retained=None, blocked=None, q=None) -> int:
        if self.database is None:
            offset = 0
            while True:
                rows = self.query(status=status,archived=archived,project_ref=project_ref,
                                  retained=retained,blocked=blocked,q=q,limit=500,offset=offset)
                offset += len(rows)
                if len(rows) < 500:
                    return offset
        where,args = self._query_filter(status=status,archived=archived,project_ref=project_ref,
                                        retained=retained,blocked=blocked,q=q)
        with self.database.connection() as conn:
            return conn.execute(f"SELECT count(*) FROM tasks{where}",args).fetchone()[0]

    def lease_summary(self, task_id: str) -> dict[str, object] | None:
        """Safe presentation metadata; never expose the mutation-authorizing lease ID."""
        record = self.get(task_id)
        directory = self._archived_dir(task_id) if record.archived_at else self._current_dir(task_id)
        lease = self._read_execution_lease(directory)
        if lease is None or parse_timestamp(lease.expires_at) <= self._now():
            return None
        return {"executor_id":lease.executor_id,"expires_at":lease.expires_at}

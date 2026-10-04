from __future__ import annotations

import hashlib
import sqlite3

from .database import ReachDatabase, database_for, timestamp
import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

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
RunExecutionMode = Literal["sync", "async"]
RunExecutionClass = Literal["normal", "heavy"]

_RUN_ID = re.compile(r"^run-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")
_TERMINAL_CLEANUP_STATUSES = {"succeeded", "remote_error", "transport_error", "timeout", "local_error"}
_AMBIGUOUS_STATUSES = {"transport_error", "timeout", "interrupted", "unknown"}
RUN_SCHEMA_VERSION = 4
PURPOSE_MAX_LENGTH = 512
RESULT_SUMMARY_MAX_LENGTH = 512
RESULT_REF_MAX_LENGTH = 512
RESULT_SUMMARY_TRUNCATION_SUFFIX = " [truncated]"


def normalize_run_purpose(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("purpose must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("purpose must not be empty")
    if len(normalized) > PURPOSE_MAX_LENGTH:
        raise ValueError(f"purpose must be at most {PURPOSE_MAX_LENGTH} characters")
    if not normalized.isprintable():
        raise ValueError("purpose must be a single printable line")
    return normalized


def normalize_result_ref(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("result_ref must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("result_ref must not be empty")
    if len(normalized) > RESULT_REF_MAX_LENGTH:
        raise ValueError(f"result_ref must be at most {RESULT_REF_MAX_LENGTH} characters")
    if not normalized.isprintable():
        raise ValueError("result_ref must be one printable line")
    return normalized


def _bounded_result_summary(value: str) -> str:
    if len(value) <= RESULT_SUMMARY_MAX_LENGTH:
        return value
    keep = RESULT_SUMMARY_MAX_LENGTH - len(RESULT_SUMMARY_TRUNCATION_SUFFIX)
    return value[:keep] + RESULT_SUMMARY_TRUNCATION_SUFFIX


def _stream_metadata(execution: dict[str, Any]) -> str:
    stdout = execution.get("stdout") if isinstance(execution.get("stdout"), dict) else {}
    stderr = execution.get("stderr") if isinstance(execution.get("stderr"), dict) else {}
    return (
        f"Output content was not persisted; observed stdout_bytes={stdout.get('bytes')}, "
        f"stderr_bytes={stderr.get('bytes')}, stdout_truncated={stdout.get('truncated')}, "
        f"stderr_truncated={stderr.get('truncated')}."
    )


def _execution_result_summary(record: "RunRecord", execution: dict[str, Any]) -> str:
    status = execution.get("status")
    exit_code = execution.get("exit_code")
    if status == "succeeded":
        lead = f"Execution succeeded with exit_code={exit_code}."
    elif status == "remote_error":
        lead = f"Remote execution failed with exit_code={exit_code}."
    elif status == "transport_error":
        lead = "SSH transport failed before a trustworthy remote result was available."
    elif status == "timeout":
        lead = f"Execution timed out after {record.timeout_seconds} seconds."
    else:
        lead = f"Execution ended with status={status}."
    ambiguity = (
        " A mutating operation may have an ambiguous outcome."
        if record.may_mutate and status in _AMBIGUOUS_STATUSES
        else ""
    )
    return _bounded_result_summary(f"{lead} {_stream_metadata(execution)}{ambiguity}")


def _internal_result_summary(record: "RunRecord", status: str, error_type: str) -> str:
    if status == "local_error":
        lead = f"Execution failed locally before a remote result ({error_type})."
    elif status == "interrupted":
        lead = f"Execution was interrupted ({error_type})."
    else:
        lead = f"Execution outcome is unknown ({error_type})."
    ambiguity = (
        " A mutating operation may have an ambiguous outcome."
        if record.may_mutate and status in _AMBIGUOUS_STATUSES
        else ""
    )
    return _bounded_result_summary(
        f"{lead} No command, script, argument values, environment values, or output content were persisted.{ambiguity}"
    )


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

    schema_version: Literal[1, 2, 3, 4] = RUN_SCHEMA_VERSION
    id: str
    operation: RunOperation
    target: str
    execution_mode: RunExecutionMode = "sync"
    execution_class: RunExecutionClass = "normal"
    purpose: str | None = Field(default=None, min_length=1, max_length=PURPOSE_MAX_LENGTH, strict=True)
    result_summary: str | None = Field(default=None, max_length=RESULT_SUMMARY_MAX_LENGTH, strict=True)
    result_ref: str | None = Field(default=None, max_length=RESULT_REF_MAX_LENGTH, strict=True)
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

    @field_validator("purpose", mode="before")
    @classmethod
    def validate_purpose(cls, value: object) -> object:
        if value is None:
            return None
        return normalize_run_purpose(value)

    @field_validator("result_ref", mode="before")
    @classmethod
    def validate_result_ref(cls, value: object) -> object:
        if value is None:
            return None
        return normalize_result_ref(value)

    @field_validator("result_summary")
    @classmethod
    def validate_result_summary(cls, value: str | None) -> str | None:
        if value is not None and (not value or not value.isprintable()):
            raise ValueError("result_summary must be one printable line when present")
        return value

    @model_validator(mode="after")
    def validate_schema_fields(self) -> "RunRecord":
        if self.schema_version == 1 and (self.purpose is not None or self.result_summary is not None):
            raise ValueError("Run schema v1 cannot contain purpose or result_summary")
        if self.schema_version < 3 and self.execution_mode != "sync":
            raise ValueError("Run schemas before v3 cannot contain async execution ownership")
        if self.schema_version < 4 and self.result_ref is not None:
            raise ValueError("Run schemas before v4 cannot contain result_ref")
        if self.schema_version < 4 and self.execution_class != "normal":
            raise ValueError("Run schemas before v4 cannot contain a non-default execution_class")
        return self

    def summary(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "operation": self.operation,
            "target": self.target,
            "execution_mode": self.execution_mode,
            "execution_class": self.execution_class,
            "purpose": self.purpose,
            "result_summary": self.result_summary,
            "result_ref": self.result_ref,
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
        read_only: bool = False,
        reconcile_modes: set[RunExecutionMode] | None = None,
        database: ReachDatabase | str | Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.completed_days = completed_days
        self._now = now
        self.read_only = read_only
        self.reconcile_modes = reconcile_modes if reconcile_modes is not None else {"sync", "async"}
        self.write_lock_path = self.root / ".write.lock"
        self._unified = database_for(database, read_only=read_only) if database is not None else None
        self.database_path = self._unified.path if self._unified is not None else self.root / "runs.sqlite3"
        self._database_seen = self.database_path.exists()
        if self._unified is not None:
            self._unified.validate()
            if not self.read_only:
                self.reconcile_incomplete()
        elif not self.read_only:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
            self._initialize_database()
            self.reconcile_incomplete()

    @property
    def _legacy_read_only(self) -> bool:
        # Read models may be constructed before service lifespan migrates Runs.
        # Once observed, a missing database is an error, never stale JSON fallback.
        if self._unified is not None:
            return False
        self._database_seen = self._database_seen or self.database_path.exists()
        return self.read_only and not self._database_seen

    def _path(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run ID")
        return self.root / f"{run_id}.json"

    def _require_writable(self) -> None:
        if self.read_only:
            raise RuntimeError("run store is read-only")

    @contextmanager
    def _write_lock(self):
        self._require_writable()
        if self._unified is not None:
            with self._unified.connection(write=True):
                yield
            return
        fd = os.open(self.write_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _serialize(record: RunRecord) -> str:
        serialized = record.model_dump()
        if record.schema_version == 1:
            serialized.pop("purpose", None)
            serialized.pop("result_summary", None)
        if record.schema_version < 3:
            serialized.pop("execution_mode", None)
        if record.schema_version < 4:
            serialized.pop("result_ref", None)
            serialized.pop("execution_class", None)
        return json.dumps(serialized, indent=2, sort_keys=True) + "\n"

    @contextmanager
    def _database(self, *, write: bool = False):
        if self._unified is not None:
            with self._unified.connection(write=write) as connection:
                yield connection
            return
        if self.database_path.is_symlink():
            raise RuntimeError("run database must not be a symlink")
        # mode=rw never silently recreates a lost/corrupt accepted database.
        mode = "ro" if self.read_only else "rw"
        connection = sqlite3.connect(self.database_path.resolve().as_uri() + f"?mode={mode}", uri=True, timeout=5)
        try:
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise RuntimeError("unsupported run database version")
            if self.read_only:
                connection.execute("PRAGMA query_only=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _put(connection, record: RunRecord, *, unified: bool = False) -> None:
        if unified:
            connection.execute(
                "INSERT INTO runs(id,status,task_id,execution_mode,retained,ambiguous,ended_at,"
                "target,operation,execution_class,started_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status,task_id=excluded.task_id,"
                "execution_mode=excluded.execution_mode,retained=excluded.retained,ambiguous=excluded.ambiguous,"
                "ended_at=excluded.ended_at,target=excluded.target,operation=excluded.operation,"
                "execution_class=excluded.execution_class,started_at=excluded.started_at,payload=excluded.payload",
                (record.id, record.status, record.task_id, record.execution_mode, int(record.retained),
                 int(record.ambiguous), timestamp(record.ended_at) if record.ended_at else None,
                 record.target, record.operation, record.execution_class, timestamp(record.started_at),
                 RunStore._serialize(record)),
            )
            return
        connection.execute(
            "INSERT INTO runs(id,status,task_id,execution_mode,retained,ambiguous,ended_at,payload) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "status=excluded.status,task_id=excluded.task_id,execution_mode=excluded.execution_mode,"
            "retained=excluded.retained,ambiguous=excluded.ambiguous,ended_at=excluded.ended_at,payload=excluded.payload",
            (record.id, record.status, record.task_id, record.execution_mode, int(record.retained),
             int(record.ambiguous), parse_timestamp(record.ended_at).timestamp() if record.ended_at else None,
             RunStore._serialize(record)),
        )

    def _initialize_database(self) -> None:
        with self._write_lock():
            if self.database_path.exists() or self.database_path.is_symlink():
                with self._database() as connection:
                    connection.execute("SELECT id FROM runs LIMIT 1").fetchall()
                return
            temporary = self.root / f".runs-{uuid4().hex}.sqlite3"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            connection = None
            try:
                connection = sqlite3.connect(temporary)
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, status TEXT NOT NULL, task_id TEXT, "
                                   "execution_mode TEXT NOT NULL, retained INTEGER NOT NULL, ambiguous INTEGER NOT NULL, "
                                   "ended_at REAL, payload TEXT NOT NULL)")
                connection.execute("CREATE INDEX runs_status_id ON runs(status,id DESC)")
                connection.execute("CREATE INDEX runs_task_id ON runs(task_id,id DESC)")
                connection.execute("CREATE INDEX runs_task_status_id ON runs(task_id,status,id DESC)")
                connection.execute("CREATE INDEX runs_cleanup ON runs(ended_at) WHERE retained=0 AND ambiguous=0")
                connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                digest = hashlib.sha256()
                count = 0
                for path in sorted(self.root.glob("run-*.json")):
                    if path.is_symlink() or not path.is_file():
                        raise RuntimeError(f"unsafe legacy run record: {path.name}")
                    record = self._read_path(path)
                    if self._path(record.id) != path:
                        raise RuntimeError(f"legacy run identity mismatch: {path.name}")
                    self._put(connection, record)
                    digest.update(path.name.encode() + b"\0" + path.read_bytes())
                    count += 1
                receipt = {"source_format": "json", "source_records": count,
                           "source_sha256": digest.hexdigest(), "migrated_at": format_timestamp(self._now()),
                           "legacy_files_preserved": True}
                connection.execute("INSERT INTO metadata VALUES ('migration',?)", (json.dumps(receipt, sort_keys=True),))
                connection.execute("PRAGMA user_version=1")
                connection.commit()
                connection.close()
                connection = None
                with temporary.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, self.database_path)
                directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if connection is not None:
                    connection.close()
                temporary.unlink(missing_ok=True)
                Path(str(temporary) + "-journal").unlink(missing_ok=True)

    def _atomic_write(self, record: RunRecord) -> None:
        self._require_writable()
        self._path(record.id)
        with self._database(write=True) as connection:
            self._put(connection, record, unified=self._unified is not None)

    def export_json(self, destination: str | Path) -> dict[str, object]:
        """Export a consistent rollback snapshot to a new directory; never overwrite state."""
        destination = Path(destination)
        destination.mkdir(mode=0o700, parents=False, exist_ok=False)
        count = 0
        try:
            if self._legacy_read_only:
                payloads = [self._serialize(self._read_path(path)) for path in sorted(self.root.glob("run-*.json"))]
            else:
                with self._database() as connection:
                    payloads = [row[0] for row in connection.execute("SELECT payload FROM runs ORDER BY id")]
            digest = hashlib.sha256()
            for payload in payloads:
                record = RunRecord.model_validate_json(payload)
                self._path(record.id)
                path = destination / f"{record.id}.json"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                digest.update(path.name.encode() + b"\0" + payload.encode())
                count += 1
            receipt = {"records": count, "sha256": digest.hexdigest(), "format": "json"}
            (destination / "export-receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
            return receipt
        except BaseException:
            # Partial export is never published as a completed rollback snapshot.
            for path in destination.iterdir():
                path.unlink()
            destination.rmdir()
            raise

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
        execution_mode: RunExecutionMode = "sync",
        execution_class: RunExecutionClass = "normal",
        purpose: str | None = None,
        result_ref: str | None = None,
        idempotent: bool | None = None,
        task_id: str | None = None,
        script_id: str | None = None,
        script_source: str | None = None,
        script_sha256: str | None = None,
        argument_names: list[str] | None = None,
    ) -> RunRecord:
        now = self._now()
        record = RunRecord(
            schema_version=RUN_SCHEMA_VERSION,
            id=new_run_id(now),
            operation=operation,
            target=target,
            execution_mode=execution_mode,
            execution_class=execution_class,
            purpose=purpose,
            result_ref=result_ref,
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
        with self._write_lock():
            self._atomic_write(record)
        return record

    def get(self, run_id: str) -> RunRecord:
        path = self._path(run_id)
        if self._legacy_read_only:
            if not path.is_file():
                raise ValueError(f"unknown run: {run_id}")
            return self._read_path(path)
        with self._database() as connection:
            row = connection.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown run: {run_id}")
        return RunRecord.model_validate_json(row[0])

    def _query_parts(self, filters: dict[str, Any]) -> tuple[str, list[object]]:
        clauses: list[str] = []
        values: list[object] = []
        typed = {"status", "task_id", "execution_mode", "retained", "ambiguous", "ended_at"}
        all_fields = typed | {"target", "operation", "execution_class", "started_at"}
        for name, value in filters.items():
            if value is None:
                continue
            if name == "q":
                if not isinstance(value, str) or len(value) > 512:
                    raise ValueError("run query text must be at most 512 characters")
                fields = ("id", "target", "operation", "purpose", "result_summary", "result_ref", "script_id")
                expressions = ["id" if field == "id" else "json_extract(payload,'$." + field + "')" for field in fields]
                clauses.append("(" + " OR ".join("instr(lower(coalesce(" + expr + ",'')),lower(?))>0" for expr in expressions) + ")")
                values.extend([value] * len(expressions))
                continue
            time_field = next((field for field in ("started_at", "ended_at") if name.startswith(field[:-3] + "_")), None)
            if time_field is not None:
                suffix = name.rsplit("_", 1)[-1]
                if suffix not in {"after", "before"}:
                    raise ValueError("unsupported run query filter")
                expression = time_field if self._unified is not None or time_field in typed else "(julianday(json_extract(payload,'$.started_at'))-2440587.5)*86400.0"
                clauses.append(expression + (">=?" if suffix == "after" else "<=?"))
                values.append(timestamp(value))
            elif name in all_fields:
                expression = name if self._unified is not None or name in typed else "json_extract(payload,'$." + name + "')"
                clauses.append(expression + "=?")
                values.append(int(value) if name in {"retained", "ambiguous"} else value)
            else:
                raise ValueError("unsupported run query filter")
        return (" WHERE " + " AND ".join(clauses) if clauses else "", values)

    def _legacy_records(self, filters: dict[str, Any]) -> list[RunRecord]:
        records = [self._read_path(path) for path in self.root.glob("run-*.json") if path.is_file()]
        for name, value in filters.items():
            if value is None:
                continue
            if name == "q":
                records = [record for record in records if any(value.lower() in str(record.summary().get(field) or "").lower() for field in ("id", "target", "operation", "purpose", "result_summary", "result_ref", "script_id"))]
            elif name in {"started_after", "started_before", "ended_after", "ended_before"}:
                field, suffix = name.split("_")
                cutoff = timestamp(value)
                records = [record for record in records if getattr(record, field + "_at") is not None and (timestamp(getattr(record, field + "_at")) >= cutoff if suffix == "after" else timestamp(getattr(record, field + "_at")) <= cutoff)]
            else:
                records = [record for record in records if getattr(record, name) == value]
        return records

    def list(self, *, status: RunStatus | None = None, task_id: str | None = None,
             target: str | None = None, operation: str | None = None,
             execution_mode: str | None = None, execution_class: str | None = None,
             retained: bool | None = None, ambiguous: bool | None = None,
             started_after: str | None = None, started_before: str | None = None,
             ended_after: str | None = None, ended_before: str | None = None,
             q: str | None = None, limit: int = 100, offset: int = 0,
             sort: str = "id", descending: bool = True) -> list[RunRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("run list limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("run list offset must be non-negative")
        sorts = {"id", "started_at", "ended_at", "status", "target", "operation", "execution_mode", "execution_class"}
        if sort not in sorts:
            raise ValueError("unsupported run sort")
        filters = {"status": status, "task_id": task_id, "target": target, "operation": operation,
                   "execution_mode": execution_mode, "execution_class": execution_class,
                   "retained": retained, "ambiguous": ambiguous, "started_after": started_after,
                   "started_before": started_before, "ended_after": ended_after,
                   "ended_before": ended_before, "q": q}
        where, values = self._query_parts(filters)
        if self._legacy_read_only:
            records = self._legacy_records(filters)
            records.sort(key=lambda record: (getattr(record, sort) or "", record.id), reverse=descending)
            return records[offset:offset + limit]
        expression = sort if self._unified is not None or sort in {"id", "status", "execution_mode", "ended_at"} else "json_extract(payload,'$." + sort + "')"
        direction = " DESC" if descending else " ASC"
        with self._database() as connection:
            rows = connection.execute("SELECT payload FROM runs" + where + " ORDER BY " + expression + direction + ",id" + direction + " LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
        return [RunRecord.model_validate_json(row[0]) for row in rows]

    def query(self, *, sort: str = "started_at", **kwargs: Any) -> list[RunRecord]:
        return self.list(sort=sort, **kwargs)

    def count(self, **filters: Any) -> int:
        where, values = self._query_parts(filters)
        if self._legacy_read_only:
            return len(self._legacy_records(filters))
        with self._database() as connection:
            return connection.execute("SELECT count(*) FROM runs" + where, values).fetchone()[0]

    def recent(self, *, limit: int = 20) -> list[RunRecord]:
        if limit < 1 or limit > 100:
            raise ValueError("recent run limit must be between 1 and 100")
        return self.list(limit=limit)

    def finish(self, run_id: str, execution: dict[str, Any]) -> RunRecord:
        with self._write_lock():
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
                    "result_summary": (
                        _execution_result_summary(record, execution)
                        if record.schema_version >= 2
                        else record.result_summary
                    ),
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
        with self._write_lock():
            record = self.get(run_id)
            if record.status != "running":
                return record
            safe_error_type = error_type[:200]
            updated = record.model_copy(
                update={
                    "ended_at": format_timestamp(self._now()),
                    "status": status,
                    "ambiguous": bool(record.may_mutate and status in _AMBIGUOUS_STATUSES),
                    "error_type": safe_error_type,
                    "result_summary": (
                        _internal_result_summary(record, status, safe_error_type)
                        if record.schema_version >= 2
                        else record.result_summary
                    ),
                }
            )
            self._atomic_write(updated)
            return updated

    def set_retained(self, run_id: str, retained: bool) -> RunRecord:
        with self._write_lock():
            record = self.get(run_id)
            updated = record.model_copy(update={"retained": retained})
            self._atomic_write(updated)
            return updated

    def reconcile_incomplete(self) -> int:
        self._require_writable()
        with self._database() as connection:
            rows = connection.execute("SELECT id,execution_mode FROM runs WHERE status='running' ORDER BY id").fetchall()
        reconciled = 0
        for run_id, mode in rows:
            if mode in self.reconcile_modes:
                self.interrupt(run_id, error_type="ExecutorRestart" if mode == "async" else "ServerRestart")
                reconciled += 1
        return reconciled

    def cleanup(self) -> list[str]:
        self._require_writable()
        if self.completed_days is None:
            return []
        cutoff = (self._now() - timedelta(days=self.completed_days)).timestamp()
        statuses = sorted(_TERMINAL_CLEANUP_STATUSES)
        with self._write_lock(), self._database(write=True) as connection:
            rows = connection.execute(
                "SELECT id FROM runs WHERE retained=0 AND ambiguous=0 AND ended_at<=? AND status IN ("
                + ",".join("?" for _ in statuses) + ") ORDER BY id", (cutoff, *statuses),
            ).fetchall()
            connection.executemany("DELETE FROM runs WHERE id=?", rows)
        return [row[0] for row in rows]

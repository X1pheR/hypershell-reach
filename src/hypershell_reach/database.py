"""Peer-local operational persistence. No network or cross-peer coordination."""
from __future__ import annotations

from contextlib import contextmanager
import asyncio
from threading import get_ident
from contextvars import ContextVar
from datetime import datetime
import os
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4

SCHEMA_VERSION = 2
_ACTIVE: ContextVar[dict[str, tuple[sqlite3.Connection, bool, tuple[int, int | None]]]] = ContextVar("reach_database_transactions", default={})


def _execution_owner() -> tuple[int, int | None]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return get_ident(), id(task) if task is not None else None


def timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must contain a timezone")
    return parsed.timestamp()


SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE runs (id TEXT PRIMARY KEY, status TEXT NOT NULL, task_id TEXT,
 execution_mode TEXT NOT NULL, retained INTEGER NOT NULL CHECK(retained IN (0,1)),
 ambiguous INTEGER NOT NULL CHECK(ambiguous IN (0,1)), ended_at REAL,
 target TEXT NOT NULL, operation TEXT NOT NULL, execution_class TEXT NOT NULL,
 started_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE INDEX runs_status_id ON runs(status,id DESC);
CREATE INDEX runs_task_id ON runs(task_id,id DESC);
CREATE INDEX runs_task_status_id ON runs(task_id,status,id DESC);
CREATE INDEX runs_target_id ON runs(target,id DESC);
CREATE INDEX runs_operation_id ON runs(operation,id DESC);
CREATE INDEX runs_mode_id ON runs(execution_mode,id DESC);
CREATE INDEX runs_class_id ON runs(execution_class,id DESC);
CREATE INDEX runs_started ON runs(started_at,id);
CREATE INDEX runs_ended ON runs(ended_at,id);
CREATE INDEX runs_ambiguous_id ON runs(ambiguous,id DESC);
CREATE INDEX runs_retained_id ON runs(retained,id DESC);
CREATE INDEX runs_cleanup ON runs(ended_at) WHERE retained=0 AND ambiguous=0;
CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=0),
 archived INTEGER NOT NULL CHECK(archived IN (0,1)), created_at REAL NOT NULL, updated_at REAL NOT NULL,
 archived_at REAL, project_ref TEXT, retained INTEGER NOT NULL CHECK(retained IN (0,1)),
 blocked INTEGER NOT NULL CHECK(blocked IN (0,1)), identity TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX tasks_archive_updated ON tasks(archived,updated_at DESC,id DESC);
CREATE INDEX tasks_status_updated ON tasks(status,updated_at DESC,id DESC);
CREATE INDEX tasks_project_updated ON tasks(project_ref,updated_at DESC,id DESC);
CREATE INDEX tasks_blocked_updated ON tasks(blocked,archived,updated_at DESC,id DESC);
CREATE INDEX tasks_retained_updated ON tasks(retained,updated_at DESC,id DESC);
CREATE INDEX tasks_identity ON tasks(identity,archived,status);
CREATE INDEX tasks_cleanup ON tasks(archived_at) WHERE archived=1 AND retained=0;
CREATE TABLE task_leases (task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
 executor_id TEXT NOT NULL, expires_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE INDEX task_leases_expiry ON task_leases(expires_at);
CREATE INDEX task_leases_owner ON task_leases(executor_id,expires_at);
CREATE TABLE candidates (id TEXT PRIMARY KEY, status TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1),
 recurrence_count INTEGER NOT NULL CHECK(recurrence_count>=1), owner_id TEXT NOT NULL,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE INDEX candidates_status_updated ON candidates(status,updated_at DESC,id DESC);
CREATE INDEX candidates_updated ON candidates(updated_at DESC,id DESC);
CREATE INDEX candidates_owner ON candidates(owner_id,updated_at DESC,id DESC);
CREATE INDEX candidates_recurrence ON candidates(recurrence_count DESC,id DESC);
"""


class ReachDatabase:
    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only

    @classmethod
    def create(cls, path: str | Path) -> "ReachDatabase":
        """Explicitly create an empty store; existing authority is never overwritten."""
        path = Path(path)
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        temporary = path.parent / f".{path.name}.{uuid4().hex}.staging"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            connection = sqlite3.connect(temporary)
            try:
                connection.execute("PRAGMA synchronous=FULL")
                connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + f"\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;")
            finally:
                connection.close()
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            # link publishes atomically and refuses a concurrent creator, unlike replace.
            os.link(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
            Path(str(temporary) + "-journal").unlink(missing_ok=True)
        result = cls(path)
        # WAL is enabled only after publishing, so no staging sidecar can be lost.
        connection = sqlite3.connect(path, timeout=5)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
        finally:
            connection.close()
        return result

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if write and self.read_only:
            raise RuntimeError("database is read-only")
        if self.path.is_symlink():
            raise RuntimeError("Reach database must not be a symlink")
        key = str(self.path.resolve())
        owner = _execution_owner()
        nested = _ACTIVE.get().get(key)
        # ContextVar values are copied into child tasks/to_thread. A connection
        # belongs only to its originating thread/task; inherited scopes open a
        # fresh transaction, including when the parent has already closed.
        if nested is not None and nested[2] == owner:
            connection, writable, _ = nested
            if write and not writable:
                raise RuntimeError("cannot upgrade read transaction to write")
            yield connection
            return
        mode = "ro" if self.read_only else "rw"
        connection = sqlite3.connect(self.path.resolve().as_uri() + f"?mode={mode}", uri=True, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        token = None
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise RuntimeError("unsupported Reach database version")
            if self.read_only:
                connection.execute("PRAGMA query_only=ON")
            else:
                connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            token = _ACTIVE.set({**_ACTIVE.get(), key: (connection, write, owner)})
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            if token is not None:
                _ACTIVE.reset(token)
            connection.close()

    def validate(self) -> None:
        with self.connection() as connection:
            for table in ("runs", "tasks", "task_leases", "candidates", "metadata"):
                connection.execute(f"SELECT * FROM {table} LIMIT 0")


def database_for(value: ReachDatabase | str | Path, *, read_only: bool = False) -> ReachDatabase:
    path = value.path if isinstance(value, ReachDatabase) else value
    return ReachDatabase(path, read_only=read_only)

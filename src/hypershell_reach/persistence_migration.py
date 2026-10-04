"""Offline authority migration and current-state rollback. Sources must be quiesced.

No service constructor invokes this module. Publication is one atomic, exclusive
link; original stores are never overwritten, repaired, or removed.
"""
from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
from uuid import uuid4

import yaml

from .candidates import CandidateRecord
from .database import ReachDatabase, SCHEMA_VERSION, timestamp
from .runs import RunRecord, RunStore, _RUN_ID
from .tasks import TaskExecutionLease, TaskRecord, TaskStore, _TASK_ID

TERMINAL = {"completed", "cancelled"}


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"unsafe source file: {path}")
    return path.read_bytes()


def _root(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"missing or unsafe source directory: {root}")
    return root


def _times(record, *names) -> None:
    for name in names:
        value = getattr(record, name)
        if value is not None:
            timestamp(value)


def _agree(row, values: dict) -> None:
    for key, expected in values.items():
        if row[key] != expected:
            raise RuntimeError(f"indexed state disagrees with payload: {key}")


def _run_indexes(record, *, unified: bool) -> dict:
    _times(record, "started_at", "ended_at")
    values = dict(id=record.id, status=record.status, task_id=record.task_id,
                  execution_mode=record.execution_mode, retained=int(record.retained),
                  ambiguous=int(record.ambiguous),
                  ended_at=timestamp(record.ended_at) if record.ended_at else None)
    if record.ended_at and timestamp(record.ended_at) < timestamp(record.started_at):
        raise RuntimeError("Run end precedes start")
    if unified:
        values.update(target=record.target, operation=record.operation,
                      execution_class=record.execution_class, started_at=timestamp(record.started_at))
    return values


def _task_indexes(record, archived: bool) -> dict:
    _times(record, "created_at", "updated_at", "archived_at")
    created, updated = timestamp(record.created_at), timestamp(record.updated_at)
    if updated < created or (record.archived_at and timestamp(record.archived_at) < created):
        raise RuntimeError("invalid Task timestamp ordering")
    if (not archived and record.archived_at is not None) or (archived and record.status not in TERMINAL):
        raise RuntimeError("Task archive location contradicts lifecycle")
    if not archived and record.status in TERMINAL:
        raise RuntimeError("terminal Task in active store requires explicit repair")
    return dict(id=record.id, status=record.status, revision=record.revision,
                archived=int(archived), created_at=created, updated_at=updated,
                archived_at=timestamp(record.archived_at) if record.archived_at else None,
                project_ref=record.project_ref, retained=int(record.retained),
                blocked=int(bool(record.continuity.blockers)),
                identity=json.dumps(TaskStore._continuity_identity(record.title, record.objective, record.project_ref)))


def _candidate_indexes(record) -> dict:
    _times(record, "created_at", "updated_at")
    if timestamp(record.updated_at) < timestamp(record.created_at):
        raise RuntimeError("invalid Candidate timestamp ordering")
    return dict(id=record.id, status=record.promotion.state, revision=record.revision,
                recurrence_count=record.problem.recurrence_count, owner_id=record.ownership.owner_id,
                created_at=timestamp(record.created_at), updated_at=timestamp(record.updated_at))


def _receipt(snapshot: dict) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_authority": snapshot["candidate_authority"],
        "counts": {name: len(snapshot[name]) for name in ("runs", "tasks", "task_leases", "candidates")},
        "task_active": sum(not item["archived"] for item in snapshot["tasks"]),
        "task_archived": sum(item["archived"] for item in snapshot["tasks"]),
        "archive_timestamp_absent": [item["payload"]["id"] for item in snapshot["tasks"]
                                     if item["archived"] and item["payload"]["archived_at"] is None],
        "semantic_sha256": hashlib.sha256(canonical(snapshot).encode()).hexdigest(),
        "entity_sha256": {name: hashlib.sha256(canonical(snapshot[name]).encode()).hexdigest()
                          for name in ("runs", "tasks", "task_leases", "candidates")},
    }


def inspect_legacy(*, runs_root=None, tasks_root=None, archive_root=None, candidates_root=None) -> dict:
    """Read strict production-shaped v0.10 state without invoking mutating stores."""
    snapshot = {name: [] for name in ("runs", "tasks", "task_leases", "candidates")}
    snapshot.update(candidate_authority=candidates_root is not None, run_metadata={}, evidence={})
    root = _root(runs_root)
    if root is not None:
        source = root / "runs.sqlite3"
        if source.is_symlink() or not source.is_file():
            raise RuntimeError("v0.10 source Run database is missing or unsafe")
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise RuntimeError("unsupported source Run schema")
            if [tuple(row) for row in conn.execute("PRAGMA integrity_check")] != [("ok",)]:
                raise RuntimeError("source Run integrity failure")
            for row in conn.execute("SELECT * FROM runs ORDER BY id"):
                record = RunRecord.model_validate_json(row["payload"])
                _agree(row, _run_indexes(record, unified=False))
                if record.id != row["id"] or not _RUN_ID.fullmatch(record.id):
                    raise RuntimeError("invalid or conflicting Run identity")
                _times(record, "started_at", "ended_at")
                snapshot["runs"].append(record.model_dump(mode="json"))
            snapshot["run_metadata"] = dict(conn.execute("SELECT key,value FROM metadata ORDER BY key"))
    seen = set()
    for archived, source in ((False, tasks_root), (True, archive_root)):
        root = _root(source)
        if root is None:
            continue
        for directory in sorted(root.iterdir()):
            if directory.name == ".locks":
                if directory.is_symlink() or not directory.is_dir():
                    raise RuntimeError("unsafe lock directory")
                continue
            if directory.is_symlink() or not directory.is_dir() or not _TASK_ID.fullmatch(directory.name):
                raise RuntimeError(f"invalid Task directory: {directory.name}")
            unknown = {p.name for p in directory.iterdir()} - {"task.yaml", "execution-lease.yaml", "evidence"}
            if unknown:
                raise RuntimeError(f"unrecognized Task state: {directory.name}")
            record = TaskRecord.model_validate(yaml.safe_load(_file(directory / "task.yaml")))
            if record.id != directory.name or record.id in seen:
                raise RuntimeError("duplicate or conflicting Task identity")
            seen.add(record.id)
            _task_indexes(record, archived)
            if (not archived and record.archived_at is not None) or (archived and record.status not in TERMINAL):
                raise RuntimeError("Task archive location contradicts lifecycle")
            if not archived and record.status in TERMINAL:
                raise RuntimeError("terminal Task in active store requires explicit repair")
            snapshot["tasks"].append({"archived": archived, "payload": record.model_dump(mode="json")})
            lease_path = directory / "execution-lease.yaml"
            if lease_path.exists() or lease_path.is_symlink():
                lease = TaskExecutionLease.model_validate(yaml.safe_load(_file(lease_path)))
                _times(lease, "acquired_at", "refreshed_at", "expires_at")
                if archived or timestamp(lease.expires_at) < timestamp(lease.refreshed_at) or timestamp(lease.refreshed_at) < timestamp(lease.acquired_at):
                    raise RuntimeError("invalid Task lease lifecycle")
                snapshot["task_leases"].append({"task_id": record.id, "payload": lease.model_dump(mode="json")})
            evidence = directory / "evidence"
            if evidence.exists() or evidence.is_symlink():
                if evidence.is_symlink() or not evidence.is_dir():
                    raise RuntimeError("unsafe Task evidence")
                files = {}
                for path in sorted(evidence.rglob("*")):
                    relative = path.relative_to(evidence).as_posix()
                    if path.is_symlink():
                        raise RuntimeError("unsafe Task evidence link")
                    files[relative + "/" if path.is_dir() else relative] = None if path.is_dir() else base64.b64encode(_file(path)).decode()
                snapshot["evidence"][record.id] = files
    root = _root(candidates_root)
    if root is not None:
        seen = set()
        for path in sorted(root.iterdir()):
            if path.name == ".locks":
                if path.is_symlink() or not path.is_dir():
                    raise RuntimeError("unsafe Candidate locks")
                continue
            if path.suffix != ".yaml":
                raise RuntimeError("unrecognized Candidate state")
            record = CandidateRecord.model_validate(yaml.safe_load(_file(path)))
            if record.id in seen or path.stem != record.id:
                raise RuntimeError("duplicate or conflicting Candidate identity")
            seen.add(record.id)
            _candidate_indexes(record)
            snapshot["candidates"].append(record.model_dump(mode="json"))
    snapshot["tasks"].sort(key=lambda row: row["payload"]["id"])
    snapshot["task_leases"].sort(key=lambda row: row["task_id"])
    return snapshot


def inspect_database(database_path) -> dict:
    snapshot = {name: [] for name in ("runs", "tasks", "task_leases", "candidates")}
    with ReachDatabase(database_path, read_only=True).connection() as conn:
        conn.row_factory = sqlite3.Row
        if [tuple(row) for row in conn.execute("PRAGMA integrity_check")] != [("ok",)]:
            raise RuntimeError("Reach database integrity failure")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Reach database foreign key failure")
        metadata = dict(conn.execute("SELECT key,value FROM metadata"))
        authority = metadata.get("candidate_authority")
        if authority not in {"true", "false"}:
            raise RuntimeError("missing or invalid Candidate authority metadata")
        snapshot["candidate_authority"] = authority == "true"
        snapshot["run_metadata"] = json.loads(metadata.get("legacy_run_metadata", "{}"))
        snapshot["evidence"] = json.loads(metadata.get("legacy_task_evidence", "{}"))
        for table, model in (("runs", RunRecord), ("candidates", CandidateRecord)):
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY id"):
                record = model.model_validate_json(row["payload"])
                _agree(row, _run_indexes(record, unified=True) if table == "runs" else _candidate_indexes(record))
                if record.id != row["id"]:
                    raise RuntimeError(f"{table} identity mismatch")
                _times(record, *("started_at", "ended_at") if table == "runs" else ("created_at", "updated_at"))
                if table == "runs" and not _RUN_ID.fullmatch(record.id):
                    raise RuntimeError("invalid Run identity")
                snapshot[table].append(record.model_dump(mode="json"))
        for row in conn.execute("SELECT * FROM tasks ORDER BY id"):
            record = TaskRecord.model_validate_json(row["payload"])
            if not _TASK_ID.fullmatch(record.id) or record.id != row["id"]:
                raise RuntimeError("Task identity mismatch")
            _agree(row, _task_indexes(record, bool(row["archived"])))
            snapshot["tasks"].append({"archived": bool(row["archived"]), "payload": record.model_dump(mode="json")})
        for row in conn.execute("SELECT * FROM task_leases ORDER BY task_id"):
            lease = TaskExecutionLease.model_validate_json(row["payload"])
            _times(lease, "acquired_at", "refreshed_at", "expires_at")
            if timestamp(lease.acquired_at) > timestamp(lease.refreshed_at) or timestamp(lease.refreshed_at) > timestamp(lease.expires_at):
                raise RuntimeError("invalid Task lease lifecycle")
            if not any(item["payload"]["id"] == row["task_id"] and not item["archived"] for item in snapshot["tasks"]):
                raise RuntimeError("lease requires active Task")
            _agree(row, dict(executor_id=lease.executor_id, expires_at=timestamp(lease.expires_at)))
            snapshot["task_leases"].append({"task_id": row["task_id"], "payload": lease.model_dump(mode="json")})
    if not snapshot["candidate_authority"] and snapshot["candidates"]:
        raise RuntimeError("Candidate state exists without configured authority")
    # Removed Tasks must not keep rollback evidence alive indefinitely.
    ids = {row["payload"]["id"] for row in snapshot["tasks"]}
    snapshot["evidence"] = {key: value for key, value in snapshot["evidence"].items() if key in ids}
    return snapshot


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _checkpoint(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result[0]:
            raise RuntimeError("migration checkpoint busy")
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def migrate_persistence(destination, *, runs_root=None, tasks_root=None, archive_root=None, candidates_root=None) -> dict:
    inputs = dict(runs_root=runs_root, tasks_root=tasks_root, archive_root=archive_root, candidates_root=candidates_root)
    snapshot = inspect_legacy(**inputs)
    receipt = _receipt(snapshot)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink():
            raise RuntimeError("unsafe migration destination")
        with ReachDatabase(destination, read_only=True).connection() as conn:
            prior = conn.execute("SELECT value FROM metadata WHERE key='unified_migration'").fetchone()
        if prior is None or json.loads(prior[0]) != receipt or _receipt(inspect_database(destination)) != receipt:
            raise RuntimeError("existing destination is not an unchanged equivalent migration")
        return {**receipt, "already_migrated": True}
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    stage = destination.parent / f".{destination.name}.migration-{uuid4().hex}"
    try:
        db = ReachDatabase.create(stage)
        with db.connection(write=True) as conn:
            for item in snapshot["runs"]:
                RunStore._put(conn, RunRecord.model_validate(item), unified=True)
            for item in snapshot["tasks"]:
                record = TaskRecord.model_validate(item["payload"])
                identity = json.dumps(TaskStore._continuity_identity(record.title, record.objective, record.project_ref))
                conn.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                    record.id, record.status, record.revision, int(item["archived"]),
                    timestamp(record.created_at), timestamp(record.updated_at),
                    timestamp(record.archived_at) if record.archived_at else None,
                    record.project_ref, int(record.retained), int(bool(record.continuity.blockers)),
                    identity, canonical(item["payload"]),
                ))
            for item in snapshot["task_leases"]:
                lease = item["payload"]
                conn.execute("INSERT INTO task_leases VALUES(?,?,?,?)", (
                    item["task_id"], lease["executor_id"], timestamp(lease["expires_at"]), canonical(lease)))
            for item in snapshot["candidates"]:
                conn.execute("INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)", (
                    item["id"], item["promotion"]["state"], item["revision"], item["problem"]["recurrence_count"],
                    item["ownership"]["owner_id"], timestamp(item["created_at"]), timestamp(item["updated_at"]), canonical(item)))
            conn.executemany("INSERT INTO metadata VALUES(?,?)", [
                ("candidate_authority", canonical(snapshot["candidate_authority"])),
                ("legacy_run_metadata", canonical(snapshot["run_metadata"])),
                ("legacy_task_evidence", canonical(snapshot["evidence"])),
                ("unified_migration", canonical(receipt)),
            ])
        if _receipt(inspect_database(stage)) != receipt:
            raise RuntimeError("migration semantic verification failed")
        if _receipt(inspect_legacy(**inputs)) != receipt:
            raise RuntimeError("source changed during migration; stop all source writers")
        _checkpoint(stage)
        os.link(stage, destination)
        _fsync_directory(destination.parent)
        return {**receipt, "already_migrated": False}
    finally:
        stage.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(stage) + suffix).unlink(missing_ok=True)


def _publish_directory(stage: Path, destination: Path) -> None:
    """Linux renameat2 publishes a directory atomically without overwriting races."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise RuntimeError("atomic exclusive directory publication requires renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        if code in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise RuntimeError("filesystem lacks atomic exclusive directory publication")
        raise OSError(code, os.strerror(code), str(destination))


def export_legacy(database_path, destination) -> dict:
    """Export a consistent *current* unified snapshot, including post-migration writes.

    The new destination is a complete v0.10 store layout. Never restore over live
    stores: point an offline old release at these roots after verification.
    """
    snapshot = inspect_database(database_path)
    receipt = _receipt(snapshot)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    stage = destination.parent / f".{destination.name}.export-{uuid4().hex}"
    stage.mkdir(mode=0o700)
    try:
        runs = stage / "runs"
        runs.mkdir()
        with sqlite3.connect(runs / "runs.sqlite3") as conn:
            conn.executescript("CREATE TABLE runs(id TEXT PRIMARY KEY,status TEXT NOT NULL,task_id TEXT,execution_mode TEXT NOT NULL,retained INTEGER NOT NULL,ambiguous INTEGER NOT NULL,ended_at REAL,payload TEXT NOT NULL); CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL); PRAGMA user_version=1;")
            for item in snapshot["runs"]:
                record = RunRecord.model_validate(item)
                conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)", (
                    record.id, record.status, record.task_id, record.execution_mode, int(record.retained), int(record.ambiguous),
                    timestamp(record.ended_at) if record.ended_at else None, RunStore._serialize(record)))
            conn.executemany("INSERT INTO metadata VALUES(?,?)", snapshot["run_metadata"].items())
            conn.executescript("CREATE INDEX runs_status_id ON runs(status,id DESC); CREATE INDEX runs_task_id ON runs(task_id,id DESC); CREATE INDEX runs_task_status_id ON runs(task_id,status,id DESC); CREATE INDEX runs_cleanup ON runs(ended_at) WHERE retained=0 AND ambiguous=0;")
        for location in ("active", "archive"):
            (stage / "tasks" / location).mkdir(parents=True)
        for item in snapshot["tasks"]:
            directory = stage / "tasks" / ("archive" if item["archived"] else "active") / item["payload"]["id"]
            directory.mkdir()
            (directory / "task.yaml").write_text(yaml.safe_dump(item["payload"], sort_keys=False))
            for relative, encoded in snapshot["evidence"].get(item["payload"]["id"], {}).items():
                relative_path = PurePosixPath(relative)
                if relative_path.is_absolute() or ".." in relative_path.parts or not relative_path.parts:
                    raise RuntimeError("unsafe rollback evidence path")
                path = directory / "evidence" / relative
                if encoded is None:
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(base64.b64decode(encoded, validate=True))
            if item["payload"]["id"] in snapshot["evidence"]:
                (directory / "evidence").mkdir(exist_ok=True)
        for item in snapshot["task_leases"]:
            (stage / "tasks" / "active" / item["task_id"] / "execution-lease.yaml").write_text(yaml.safe_dump(item["payload"], sort_keys=False))
        if snapshot["candidate_authority"]:
            (stage / "candidates").mkdir()
            for item in snapshot["candidates"]:
                (stage / "candidates" / f"{item['id']}.yaml").write_text(yaml.safe_dump(item, sort_keys=False))
        restored = inspect_legacy(runs_root=runs, tasks_root=stage / "tasks" / "active", archive_root=stage / "tasks" / "archive",
                                  candidates_root=stage / "candidates" if snapshot["candidate_authority"] else None)
        if _receipt(restored) != receipt:
            raise RuntimeError("rollback export semantic verification failed")
        (stage / "rollback-receipt.json").write_text(canonical(receipt) + "\n")
        for path in stage.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o600)
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
        for path in sorted((p for p in stage.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            _fsync_directory(path)
        _fsync_directory(stage)
        _publish_directory(stage, destination)
        _fsync_directory(destination.parent)
        return receipt
    finally:
        if stage.exists():
            shutil.rmtree(stage)

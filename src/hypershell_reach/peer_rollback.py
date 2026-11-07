"""Offline-compatible, non-destructive export for the pre-peer-sync Reach reader."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from .config import ReachConfig
from .tasks import TaskRecord


def export_pre_peer_state(config: ReachConfig, output: str | Path) -> dict[str, object]:
    """Create new consistent SQLite backup, translating schema-v3 transferred Tasks.

    The caller must quiesce the serving peer before using the export for
    rollback. This method only creates an output and never replaces source.
    """
    if not config.workspace.database:
        raise ValueError("unified database is required")
    source = Path(config.workspace.database)
    destination = Path(output)
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ValueError("rollback export destination must be a new absolute path")
    if not destination.parent.is_dir():
        raise ValueError("rollback export parent directory does not exist")
    if source.resolve() == destination.resolve():
        raise ValueError("cannot overwrite active Reach database")

    stage = destination.parent / f".{destination.name}.{uuid4().hex}.staging"
    if stage.exists() or stage.is_symlink():
        raise ValueError("rollback staging collision")
    source_connection = None
    target_connection = None
    transformed = 0
    try:
        source_connection = sqlite3.connect(
            source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        target_connection = sqlite3.connect(stage)
        os.chmod(stage, 0o600)
        source_connection.backup(target_connection)
        source_connection.close()
        source_connection = None
        target_connection.close()
        target_connection = None

        target_connection = sqlite3.connect(stage)
        target_connection.row_factory = sqlite3.Row
        with target_connection:
            rows = target_connection.execute(
                "SELECT id,payload FROM tasks WHERE status='transferred' ORDER BY id"
            ).fetchall()
            for row in rows:
                record = TaskRecord.model_validate_json(row["payload"])
                if record.schema_version != 3 or not record.archived_at:
                    raise ValueError("cannot downgrade inconsistent transferred Task")
                # The origin-to-Home mapping remains in the database metadata
                # for future forward recovery; older Reach can read cancellation.
                back = record.model_copy(update={"schema_version": 2, "status": "cancelled"})
                target_connection.execute(
                    "UPDATE tasks SET status='cancelled',payload=? WHERE id=? AND status='transferred'",
                    (back.model_dump_json(), record.id),
                )
                transformed += 1
            if target_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("rollback export integrity check failed")
            if target_connection.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise ValueError("rollback export has unexpected database schema")
            if target_connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='transferred'"
            ).fetchone()[0] != 0:
                raise ValueError("rollback export retained incompatible transferred Tasks")
        target_connection.close()
        target_connection = None
        with open(stage, "rb") as handle:
            os.fsync(handle.fileno())
        os.link(stage, destination)  # fail closed on an existing destination
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {"status": "exported", "path": str(destination),
                "translated_tasks": transformed, "source_unchanged": True}
    finally:
        if source_connection is not None:
            source_connection.close()
        if target_connection is not None:
            target_connection.close()
        stage.unlink(missing_ok=True)
        Path(str(stage) + "-journal").unlink(missing_ok=True)
        Path(str(stage) + "-wal").unlink(missing_ok=True)
        Path(str(stage) + "-shm").unlink(missing_ok=True)

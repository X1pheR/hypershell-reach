from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .tasks import TaskRecord

TaskLocation = Literal["active", "archive"]
EvidenceState = Literal["absent", "empty", "nonempty"]
_OPEN_STATUSES = {"active", "partial", "blocked"}
_TERMINAL_STATUSES = {"completed", "cancelled"}


class TaskMigrationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_location: TaskLocation
    target_location: TaskLocation
    schema_version: int = Field(ge=1)
    revision: int = Field(ge=0)
    status: str
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_state: EvidenceState
    evidence_entries: dict[str, str] = Field(default_factory=dict)


class TaskMigrationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    active_count: int = Field(ge=0)
    archive_count: int = Field(ge=0)
    task_count: int = Field(ge=0)
    entries: list[TaskMigrationEntry]
    preimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TaskMigrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source_preimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_active_count: int = Field(ge=0)
    source_archive_count: int = Field(ge=0)
    target_active_count: int = Field(ge=0)
    target_archive_count: int = Field(ge=0)
    reconciled_terminal_ids: list[str]
    removed_empty_evidence_ids: list[str]
    preserved_nonempty_evidence_ids: list[str]
    source_untouched: bool
    switch_ready: bool


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_tree(path: Path) -> tuple[EvidenceState, dict[str, str]]:
    if not path.exists():
        return "absent", {}
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"invalid evidence directory: {path}")
    direct = list(path.iterdir())
    if not direct:
        return "empty", {}
    entries: dict[str, str] = {}
    for item in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
        relative = item.relative_to(path).as_posix()
        if item.is_symlink():
            raise RuntimeError(f"evidence symlinks are not supported: {relative}")
        if item.is_dir():
            entries[f"{relative}/"] = "dir"
        elif item.is_file():
            entries[relative] = f"sha256:{_sha256_file(item)}"
        else:
            raise RuntimeError(f"unsupported evidence entry: {relative}")
    return "nonempty", entries


def _read_task_entry(directory: Path, location: TaskLocation) -> TaskMigrationEntry:
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError(f"invalid task directory: {directory.name}")
    children = {child.name for child in directory.iterdir()}
    unknown = sorted(children - {"task.yaml", "evidence"})
    if unknown:
        raise RuntimeError(
            f"unexpected task directory entries for {directory.name}: {', '.join(unknown)}"
        )
    record_path = directory / "task.yaml"
    if record_path.is_symlink() or not record_path.is_file():
        raise RuntimeError(f"invalid task record: {directory.name}")
    try:
        payload = yaml.safe_load(record_path.read_text(encoding="utf-8"))
        record = TaskRecord.model_validate(payload)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        raise RuntimeError(f"invalid task record: {directory.name}") from exc
    if record.id != directory.name:
        raise RuntimeError(f"task ID does not match directory: {directory.name}")
    if location == "archive" and record.status not in _TERMINAL_STATUSES:
        raise RuntimeError(f"non-terminal task found in archive: {record.id}")
    target_location: TaskLocation = (
        "active" if location == "active" and record.status in _OPEN_STATUSES else "archive"
    )
    evidence_state, evidence_entries = _evidence_tree(directory / "evidence")
    return TaskMigrationEntry(
        id=record.id,
        source_location=location,
        target_location=target_location,
        schema_version=record.schema_version,
        revision=record.revision,
        status=record.status,
        task_sha256=_sha256_file(record_path),
        evidence_state=evidence_state,
        evidence_entries=evidence_entries,
    )


def inspect_task_storage(
    active_root: str | Path,
    archive_root: str | Path,
) -> TaskMigrationManifest:
    active = Path(active_root)
    archive = Path(archive_root)
    entries: list[TaskMigrationEntry] = []
    seen: set[str] = set()
    counts = {"active": 0, "archive": 0}
    for location, root in (("active", active), ("archive", archive)):
        if not root.exists():
            continue
        if root.is_symlink() or not root.is_dir():
            raise RuntimeError(f"invalid task storage root: {root}")
        for directory in sorted(root.glob("task-*"), key=lambda value: value.name):
            entry = _read_task_entry(directory, location)  # type: ignore[arg-type]
            if entry.id in seen:
                raise RuntimeError(f"duplicate task ID across active/archive roots: {entry.id}")
            seen.add(entry.id)
            entries.append(entry)
            counts[location] += 1
    entries.sort(key=lambda entry: entry.id)
    preimage_payload = [entry.model_dump(mode="json") for entry in entries]
    preimage = hashlib.sha256(
        json.dumps(preimage_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return TaskMigrationManifest(
        active_count=counts["active"],
        archive_count=counts["archive"],
        task_count=len(entries),
        entries=entries,
        preimage_sha256=preimage,
    )


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    files = sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: path.as_posix())
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in files:
        _fsync_file(path)
    for path in directories:
        _fsync_directory(path)
    _fsync_directory(root)


def _validate_empty_target(path: Path) -> bool:
    existed = path.exists()
    if not existed:
        return False
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"invalid migration target root: {path}")
    if any(path.iterdir()):
        raise RuntimeError(f"migration target root must be empty: {path}")
    return True


def _create_missing_target(path: Path, existed: bool) -> None:
    if not existed:
        path.mkdir(parents=True, mode=0o750)


def _rollback_target(path: Path, existed: bool) -> None:
    if path.exists():
        shutil.rmtree(path)
    if existed:
        path.mkdir(parents=True, mode=0o750)


def _copy_entry(
    entry: TaskMigrationEntry,
    source_active: Path,
    source_archive: Path,
    target_active: Path,
    target_archive: Path,
) -> None:
    source_root = source_active if entry.source_location == "active" else source_archive
    target_root = target_active if entry.target_location == "active" else target_archive
    source_dir = source_root / entry.id
    target_dir = target_root / entry.id
    target_dir.mkdir(mode=0o750)
    shutil.copy2(source_dir / "task.yaml", target_dir / "task.yaml")
    if entry.evidence_state == "nonempty":
        shutil.copytree(source_dir / "evidence", target_dir / "evidence", copy_function=shutil.copy2)
    _fsync_tree(target_dir)
    _fsync_directory(target_root)


def _validate_target_matches_source(
    source: TaskMigrationManifest,
    target: TaskMigrationManifest,
) -> None:
    if target.task_count != source.task_count:
        raise RuntimeError(
            f"task migration count mismatch: source {source.task_count}, target {target.task_count}"
        )
    target_by_id = {entry.id: entry for entry in target.entries}
    if set(target_by_id) != {entry.id for entry in source.entries}:
        raise RuntimeError("task migration ID set mismatch")
    for source_entry in source.entries:
        target_entry = target_by_id[source_entry.id]
        if target_entry.source_location != source_entry.target_location:
            raise RuntimeError(f"task migration location mismatch: {source_entry.id}")
        if target_entry.task_sha256 != source_entry.task_sha256:
            raise RuntimeError(f"task migration record hash mismatch: {source_entry.id}")
        expected_evidence_state: EvidenceState = (
            "absent" if source_entry.evidence_state == "empty" else source_entry.evidence_state
        )
        if target_entry.evidence_state != expected_evidence_state:
            raise RuntimeError(f"task migration evidence state mismatch: {source_entry.id}")
        if source_entry.evidence_state == "nonempty":
            if target_entry.evidence_entries != source_entry.evidence_entries:
                raise RuntimeError(f"task migration evidence hash mismatch: {source_entry.id}")


def copy_and_validate_task_storage(
    source_active_root: str | Path,
    source_archive_root: str | Path,
    target_active_root: str | Path,
    target_archive_root: str | Path,
) -> TaskMigrationResult:
    source_active = Path(source_active_root)
    source_archive = Path(source_archive_root)
    target_active = Path(target_active_root)
    target_archive = Path(target_archive_root)
    source_paths = {source_active.resolve(), source_archive.resolve()}
    target_paths = {target_active.resolve(), target_archive.resolve()}
    if len(target_paths) != 2 or source_paths & target_paths:
        raise ValueError("source and target Task storage roots must be distinct")

    source = inspect_task_storage(source_active, source_archive)
    active_existed = _validate_empty_target(target_active)
    archive_existed = _validate_empty_target(target_archive)
    _create_missing_target(target_active, active_existed)
    _create_missing_target(target_archive, archive_existed)
    try:
        if target_active.stat().st_dev != target_archive.stat().st_dev:
            raise RuntimeError("target active and archive roots must be on the same filesystem")
        for entry in source.entries:
            _copy_entry(entry, source_active, source_archive, target_active, target_archive)
        _fsync_directory(target_active)
        _fsync_directory(target_archive)

        source_after = inspect_task_storage(source_active, source_archive)
        if source_after.preimage_sha256 != source.preimage_sha256:
            raise RuntimeError("source Task storage changed during migration copy")
        target = inspect_task_storage(target_active, target_archive)
        _validate_target_matches_source(source, target)

        reconciled = sorted(
            entry.id
            for entry in source.entries
            if entry.source_location == "active" and entry.target_location == "archive"
        )
        removed_empty = sorted(
            entry.id for entry in source.entries if entry.evidence_state == "empty"
        )
        preserved_nonempty = sorted(
            entry.id for entry in source.entries if entry.evidence_state == "nonempty"
        )
        return TaskMigrationResult(
            source_preimage_sha256=source.preimage_sha256,
            source_active_count=source.active_count,
            source_archive_count=source.archive_count,
            target_active_count=target.active_count,
            target_archive_count=target.archive_count,
            reconciled_terminal_ids=reconciled,
            removed_empty_evidence_ids=removed_empty,
            preserved_nonempty_evidence_ids=preserved_nonempty,
            source_untouched=True,
            switch_ready=True,
        )
    except Exception:
        _rollback_target(target_active, active_existed)
        _rollback_target(target_archive, archive_existed)
        raise

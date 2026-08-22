from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from hats_mcp.runs import RunStore
from hats_mcp.task_migration import copy_and_validate_task_storage, inspect_task_storage
from hats_mcp.tasks import TaskStore


def _write_v1_task(root: Path, task_id: str, *, status: str = "active", retained: bool = False) -> Path:
    directory = root / task_id
    directory.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "id": task_id,
        "title": "Legacy v1",
        "objective": "Preserve exact legacy continuity.",
        "project_ref": "projects/legacy.md",
        "status": status,
        "next_action": "Continue" if status in {"active", "partial", "blocked"} else None,
        "continuity": {
            "authorization": "Legacy authorization context.",
            "sources": [],
            "completed": ["Legacy completed item."],
            "validation": ["Legacy validation item."],
            "cleanup": [],
            "recovery": "Use the legacy source root until cutover succeeds.",
            "blockers": [],
            "assumptions": [],
        },
        "retained": retained,
        "created_at": "2026-08-01T10:00:00.000000Z",
        "updated_at": "2026-08-02T11:00:00.000000Z",
        "archived_at": None,
    }
    path = directory / "task.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _make_terminal_residue(store: TaskStore, task_id: str, status: str = "completed") -> None:
    path = store.tasks_root / task_id / "task.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["status"] = status
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_copy_validate_migration_preserves_task_state_and_reconciles_terminal_active_residue(tmp_path) -> None:
    source_active = tmp_path / "source-active"
    source_archive = tmp_path / "source-archive"
    source = TaskStore(source_active, source_archive)
    active = source.create(title="Active", objective="Stay active.", retained=True)
    partial = source.create(title="Partial", objective="Stay partial.")
    partial = source.update(partial.id, expected_revision=1, status="partial", next_action="Resume")
    terminal = source.create(title="Residue", objective="Move to archive during migration.")
    _make_terminal_residue(source, terminal.id)
    archived = source.create(title="Archived", objective="Already archived.")
    archived = source.close(archived.id, status="cancelled", expected_revision=1)

    empty_evidence = source_active / active.id / "evidence"
    empty_evidence.mkdir()
    nonempty_evidence = source_active / partial.id / "evidence"
    (nonempty_evidence / "nested").mkdir(parents=True)
    (nonempty_evidence / "nested" / "note.txt").write_text("preserve me\n", encoding="utf-8")

    target_active = tmp_path / "target" / "active"
    target_archive = tmp_path / "target" / "archive"
    before = inspect_task_storage(source_active, source_archive)
    result = copy_and_validate_task_storage(
        source_active, source_archive, target_active, target_archive
    )
    after = inspect_task_storage(source_active, source_archive)

    assert result.switch_ready is True
    assert result.source_untouched is True
    assert result.source_preimage_sha256 == before.preimage_sha256 == after.preimage_sha256
    assert result.source_active_count == 3
    assert result.source_archive_count == 1
    assert result.target_active_count == 2
    assert result.target_archive_count == 2
    assert result.reconciled_terminal_ids == [terminal.id]
    assert result.removed_empty_evidence_ids == [active.id]
    assert result.preserved_nonempty_evidence_ids == [partial.id]

    target = TaskStore(target_active, target_archive, read_only=True)
    assert {record.status for record in target.list()} == {"active", "partial"}
    assert target.get(active.id).model_dump() == source.get(active.id).model_dump()
    assert target.get(partial.id).model_dump() == source.get(partial.id).model_dump()
    assert target.get(terminal.id).model_dump() == source.get(terminal.id).model_dump()
    assert target.get(archived.id).model_dump() == source.get(archived.id).model_dump()
    assert not (target_active / active.id / "evidence").exists()
    assert (target_active / partial.id / "evidence" / "nested" / "note.txt").read_text() == "preserve me\n"


def test_v1_migration_preserves_bytes_ids_timestamps_retained_and_continuity(tmp_path) -> None:
    source_active = tmp_path / "source-active"
    source_archive = tmp_path / "source-archive"
    task_id = "task-20260801T100000000000Z-123456789abc"
    source_path = _write_v1_task(source_active, task_id, retained=True)
    source_bytes = source_path.read_bytes()

    target_active = tmp_path / "target-active"
    target_archive = tmp_path / "target-archive"
    copy_and_validate_task_storage(source_active, source_archive, target_active, target_archive)

    target_path = target_active / task_id / "task.yaml"
    assert target_path.read_bytes() == source_bytes
    migrated = TaskStore(target_active, target_archive, read_only=True).get(task_id)
    assert migrated.schema_version == 1
    assert migrated.revision == 0
    assert migrated.created_at == "2026-08-01T10:00:00.000000Z"
    assert migrated.updated_at == "2026-08-02T11:00:00.000000Z"
    assert migrated.archived_at is None
    assert migrated.retained is True
    assert migrated.continuity.completed == ["Legacy completed item."]


def test_nonempty_legacy_evidence_is_preserved_and_surfaced_for_review(tmp_path) -> None:
    source_active = tmp_path / "source-active"
    source_archive = tmp_path / "source-archive"
    task_id = "task-20260801T100000000000Z-abcdefabcdef"
    _write_v1_task(source_active, task_id)
    evidence = source_active / task_id / "evidence"
    (evidence / "deep").mkdir(parents=True)
    (evidence / "deep" / "payload.bin").write_bytes(b"unexpected-evidence")

    target_active = tmp_path / "target-active"
    target_archive = tmp_path / "target-archive"
    result = copy_and_validate_task_storage(source_active, source_archive, target_active, target_archive)

    assert result.preserved_nonempty_evidence_ids == [task_id]
    assert (target_active / task_id / "evidence" / "deep" / "payload.bin").read_bytes() == b"unexpected-evidence"


def test_migration_rejects_malformed_or_duplicate_source_records(tmp_path) -> None:
    active = tmp_path / "active"
    archive = tmp_path / "archive"
    malformed = active / "task-20260801T100000000000Z-111111111111"
    malformed.mkdir(parents=True)
    (malformed / "task.yaml").write_text("schema_version: [broken\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid task record"):
        inspect_task_storage(active, archive)

    shutil.rmtree(active)
    task_id = "task-20260801T100000000000Z-222222222222"
    _write_v1_task(active, task_id, status="completed")
    shutil.copytree(active / task_id, archive / task_id)
    with pytest.raises(RuntimeError, match="duplicate task ID"):
        inspect_task_storage(active, archive)


def test_migration_preserves_run_task_relationship_without_task_backlinks(tmp_path) -> None:
    source_active = tmp_path / "source-active"
    source_archive = tmp_path / "source-archive"
    task = TaskStore(source_active, source_archive).create(title="Linked", objective="Keep run linkage.")
    runs_root = tmp_path / "runs"
    runs = RunStore(runs_root)
    run = runs.create(
        operation="run_shell",
        target="docker",
        timeout_seconds=30,
        may_mutate=True,
        task_id=task.id,
    )
    run_path = runs_root / f"{run.id}.json"
    before = run_path.read_bytes()

    target_active = tmp_path / "target-active"
    target_archive = tmp_path / "target-archive"
    copy_and_validate_task_storage(source_active, source_archive, target_active, target_archive)

    assert run_path.read_bytes() == before
    assert [item.id for item in RunStore(runs_root, read_only=True).list(task_id=task.id)] == [run.id]
    migrated_payload = yaml.safe_load((target_active / task.id / "task.yaml").read_text(encoding="utf-8"))
    assert "linked_run_ids" not in migrated_payload
    assert "runs" not in migrated_payload


def test_validated_copy_can_be_rolled_back_before_source_retirement(tmp_path) -> None:
    source_active = tmp_path / "source-active"
    source_archive = tmp_path / "source-archive"
    source = TaskStore(source_active, source_archive)
    task = source.create(title="Rollback", objective="Keep source authoritative until retirement.")
    before = inspect_task_storage(source_active, source_archive)

    target_active = tmp_path / "target" / "active"
    target_archive = tmp_path / "target" / "archive"
    result = copy_and_validate_task_storage(source_active, source_archive, target_active, target_archive)
    assert result.switch_ready is True
    assert TaskStore(target_active, target_archive, read_only=True).get(task.id).id == task.id

    shutil.rmtree(target_active)
    shutil.rmtree(target_archive)
    after_rollback = inspect_task_storage(source_active, source_archive)

    assert after_rollback.preimage_sha256 == before.preimage_sha256
    assert source.get(task.id).id == task.id


def test_migration_refuses_nonempty_target_without_deleting_existing_data(tmp_path) -> None:
    source_active = tmp_path / "source-active"
    source_archive = tmp_path / "source-archive"
    TaskStore(source_active, source_archive).create(
        title="Source", objective="Do not damage an occupied migration target."
    )

    target_active = tmp_path / "target" / "active"
    target_archive = tmp_path / "target" / "archive"
    target_archive.mkdir(parents=True)
    sentinel = target_archive / "do-not-delete.txt"
    sentinel.write_text("existing target data\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="migration target root must be empty"):
        copy_and_validate_task_storage(
            source_active, source_archive, target_active, target_archive
        )

    assert sentinel.read_text(encoding="utf-8") == "existing target data\n"
    assert target_archive.is_dir()

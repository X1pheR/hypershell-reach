from __future__ import annotations

import yaml
import pytest

from hypershell_reach.tasks import TaskStore


def _legacy_v1_payload(task_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": task_id,
        "title": "Legacy task",
        "objective": "Remain readable during migration.",
        "project_ref": "projects/example.md",
        "status": "active",
        "next_action": "Continue",
        "continuity": {
            "authorization": None,
            "sources": [],
            "completed": ["Captured legacy state."],
            "validation": [],
            "cleanup": [],
            "recovery": None,
            "blockers": [],
            "assumptions": [],
        },
        "retained": False,
        "created_at": "2026-08-22T12:00:00.000000Z",
        "updated_at": "2026-08-22T12:00:00.000000Z",
        "archived_at": None,
    }


def _write_legacy_v1(tasks, task_id: str):
    directory = tasks / task_id
    directory.mkdir(parents=True)
    path = directory / "task.yaml"
    path.write_text(yaml.safe_dump(_legacy_v1_payload(task_id), sort_keys=False), encoding="utf-8")
    return path


def test_v1_task_record_remains_readable_without_rewriting(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    trash = tmp_path / "archive"
    task_id = "task-20260822T120000000000Z-123456789abc"
    path = _write_legacy_v1(tasks, task_id)
    before = path.read_bytes()

    loaded = TaskStore(tasks, trash).get(task_id)

    assert loaded.schema_version == 1
    assert loaded.revision == 0
    assert loaded.continuity.completed == ["Captured legacy state."]
    assert path.read_bytes() == before


def test_new_task_schema_roundtrip_uses_v2_revision_one_and_no_storage_paths(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store = TaskStore(tasks, archive)

    created = store.create(title="New task", objective="Use the evolved schema.")
    payload = yaml.safe_load((tasks / created.id / "task.yaml").read_text(encoding="utf-8"))
    loaded = store.get(created.id)

    assert created.schema_version == 2
    assert created.revision == 1
    assert loaded == created
    assert payload["schema_version"] == 2
    assert payload["revision"] == 1
    assert "task_path" not in payload
    assert "evidence_dir" not in payload
    assert "linked_run_ids" not in payload


def test_task_record_rejects_unknown_persisted_fields(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store = TaskStore(tasks, archive)
    created = store.create(title="Strict", objective="Reject unknown state.")
    path = tasks / created.id / "task.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["arbitrary_document_mutation"] = True
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid task record"):
        store.get(created.id)


def test_task_update_rejects_stale_expected_revision(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks", tmp_path / "archive")
    created = store.create(title="CAS", objective="Reject stale writes.")

    updated = store.update(created.id, expected_revision=1, title="Committed")

    with pytest.raises(ValueError, match="stale task revision"):
        store.update(created.id, expected_revision=1, title="Lost update")

    assert updated.revision == 2
    assert store.get(created.id).title == "Committed"


def test_new_tasks_do_not_create_evidence_directories(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    store = TaskStore(tasks, tmp_path / "archive")
    created = store.create(title="No evidence", objective="Keep task state compact.")

    assert not (tasks / created.id / "evidence").exists()


def test_task_atomic_write_fsyncs_file_and_parent_directory(tmp_path, monkeypatch) -> None:
    import os
    import stat
    import hypershell_reach.tasks as tasks_module

    fsynced_modes: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        fsynced_modes.append(os.fstat(fd).st_mode)
        real_fsync(fd)

    monkeypatch.setattr(tasks_module.os, "fsync", recording_fsync)
    TaskStore(tmp_path / "tasks", tmp_path / "archive").create(
        title="Durable", objective="Fsync file and containing directory."
    )

    assert any(stat.S_ISREG(mode) for mode in fsynced_modes)
    assert any(stat.S_ISDIR(mode) for mode in fsynced_modes)


def test_task_atomic_replace_failure_preserves_committed_record(tmp_path, monkeypatch) -> None:
    import hypershell_reach.tasks as tasks_module

    store = TaskStore(tmp_path / "tasks", tmp_path / "archive")
    original = store.create(title="Original", objective="Remain committed.")

    def fail_replace(source, destination):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(tasks_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        store.update(original.id, expected_revision=1, title="Must not commit")

    assert store.get(original.id) == original
    assert not list((tmp_path / "tasks" / original.id).glob(".task.*.tmp"))


def _process_task_update(tasks_root: str, archive_root: str, task_id: str, title: str, gate, queue) -> None:
    store = TaskStore(tasks_root, archive_root)
    gate.wait(timeout=5)
    try:
        updated = store.update(task_id, expected_revision=1, title=title)
        queue.put(("ok", updated.revision, updated.title))
    except ValueError as exc:
        queue.put(("error", str(exc)))


def test_interprocess_task_updates_are_serialized_by_per_task_lock(tmp_path) -> None:
    import multiprocessing

    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    record = TaskStore(tasks, archive).create(title="Concurrent", objective="Serialize writers.")
    context = multiprocessing.get_context("fork")
    gate = context.Event()
    queue = context.Queue()
    processes = [
        context.Process(
            target=_process_task_update,
            args=(str(tasks), str(archive), record.id, title, gate, queue),
        )
        for title in ("Process one", "Process two")
    ]
    for process in processes:
        process.start()
    gate.set()
    results = [queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert sorted(result[0] for result in results) == ["error", "ok"]
    committed = TaskStore(tasks, archive).get(record.id)
    assert committed.revision == 2
    assert committed.title in {"Process one", "Process two"}


def test_terminal_update_uses_single_close_boundary_and_leaves_no_active_record(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store = TaskStore(tasks, archive)
    created = store.create(title="Close", objective="Archive on terminal transition.")

    closed = store.update(
        created.id,
        expected_revision=1,
        status="completed",
        clear_next_action=True,
    )

    assert closed.status == "completed"
    assert closed.revision == 2
    assert closed.archived_at is not None
    assert not (tasks / created.id).exists()
    assert (archive / created.id / "task.yaml").is_file()


def test_close_retry_after_committed_archive_is_idempotent_for_caller(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store = TaskStore(tasks, archive)
    created = store.create(title="Retry", objective="Retry safely.")

    first = store.close(created.id, status="cancelled", expected_revision=1)
    repeated = store.close(created.id, status="cancelled", expected_revision=1)

    assert repeated == first
    assert repeated.revision == 2
    assert not (tasks / created.id).exists()


def test_close_fsyncs_task_and_both_root_directories(tmp_path, monkeypatch) -> None:
    import os
    import hypershell_reach.tasks as tasks_module

    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store = TaskStore(tasks, archive)
    created = store.create(title="Fsync close", objective="Persist close and move.")
    fsynced_paths: list[str] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        try:
            fsynced_paths.append(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            pass
        real_fsync(fd)

    monkeypatch.setattr(tasks_module.os, "fsync", recording_fsync)
    store.close(created.id, status="completed", expected_revision=1)

    assert str(tasks / created.id) in fsynced_paths
    assert str(tasks) in fsynced_paths
    assert str(archive) in fsynced_paths


def test_repair_recovers_terminal_record_written_before_archive_move(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store = TaskStore(tasks, archive)
    created = store.create(title="Interrupted", objective="Recover interrupted close.")
    directory = tasks / created.id
    committed_terminal = created.model_copy(
        update={
            "revision": 2,
            "status": "completed",
            "updated_at": "2026-08-22T13:00:00.000000Z",
            "archived_at": "2026-08-22T13:00:00.000000Z",
        }
    )
    store._atomic_write(directory, committed_terminal)

    repaired = store.repair()

    assert repaired == [created.id]
    recovered = store.get(created.id)
    assert recovered == committed_terminal
    assert not directory.exists()
    assert (archive / created.id).is_dir()


def test_startup_repair_moves_legacy_terminal_residue_but_not_open_states(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store = TaskStore(tasks, archive)
    active = store.create(title="Active", objective="Stay active.")
    partial = store.create(title="Partial", objective="Stay partial.")
    blocked = store.create(title="Blocked", objective="Stay blocked.")
    terminal = store.create(title="Terminal", objective="Repair old residue.")
    store.update(partial.id, expected_revision=1, status="partial")
    store.update(blocked.id, expected_revision=1, status="blocked")

    terminal_path = tasks / terminal.id / "task.yaml"
    payload = yaml.safe_load(terminal_path.read_text(encoding="utf-8"))
    payload["status"] = "completed"
    payload["revision"] = 2
    terminal_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    repaired = store.repair()

    assert repaired == [terminal.id]
    assert store.require_open(active.id).status == "active"
    assert store.require_open(partial.id).status == "partial"
    assert store.require_open(blocked.id).status == "blocked"
    assert store.get(terminal.id).status == "completed"
    assert not (tasks / terminal.id).exists()


def test_duplicate_task_id_across_active_and_archive_roots_fails_safe(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store = TaskStore(tasks, archive)
    created = store.create(title="Duplicate", objective="Fail safe.")
    source = tasks / created.id
    destination = archive / created.id
    import shutil
    shutil.copytree(source, destination)

    with pytest.raises(RuntimeError, match="task exists in current and archive"):
        store.get(created.id)
    with pytest.raises(RuntimeError, match="duplicate task ID"):
        store.list(include_archived=True)
    with pytest.raises(RuntimeError, match="task exists in current and archive"):
        store.repair()


def test_close_retry_reestablishes_root_fsync_after_rename_was_committed(tmp_path, monkeypatch) -> None:
    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store = TaskStore(tasks, archive)
    created = store.create(title="Crash window", objective="Recover after rename before root fsync.")
    real_fsync_directory = store._fsync_directory
    failed_once = False

    def fail_first_tasks_root_fsync(directory):
        nonlocal failed_once
        if directory == tasks and not failed_once:
            failed_once = True
            raise OSError("simulated root fsync failure")
        real_fsync_directory(directory)

    monkeypatch.setattr(store, "_fsync_directory", fail_first_tasks_root_fsync)
    with pytest.raises(OSError, match="simulated root fsync failure"):
        store.close(created.id, status="completed", expected_revision=1)

    assert not (tasks / created.id).exists()
    assert (archive / created.id / "task.yaml").is_file()

    fsynced: list[object] = []

    def recording_fsync(directory):
        fsynced.append(directory)
        real_fsync_directory(directory)

    monkeypatch.setattr(store, "_fsync_directory", recording_fsync)
    recovered = store.close(created.id, status="completed", expected_revision=1)

    assert recovered.status == "completed"
    assert recovered.revision == 2
    assert tasks in fsynced
    assert archive in fsynced


def test_task_store_fails_safe_on_malformed_task_shaped_filesystem_entries(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store = TaskStore(tasks, archive)
    malformed_id = "task-20260822T120000000000Z-aaaaaaaaaaaa"
    (tasks / malformed_id).write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid task directory"):
        store.list()
    with pytest.raises(RuntimeError, match="invalid task directory"):
        store.repair()


def test_concurrent_task_updates_cannot_silently_lose_committed_state(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store_a = TaskStore(tasks, archive)
    store_b = TaskStore(tasks, archive)
    created = store_a.create(title="Concurrent", objective="Reject one stale writer.")

    def update(store: TaskStore, title: str):
        try:
            record = store.update(created.id, expected_revision=1, title=title)
            return ("ok", record.revision, record.title)
        except ValueError as exc:
            return ("error", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda arguments: update(*arguments),
                [(store_a, "Thread one"), (store_b, "Thread two")],
            )
        )

    assert sorted(result[0] for result in results) == ["error", "ok"]
    committed = store_a.get(created.id)
    assert committed.revision == 2
    assert committed.title in {"Thread one", "Thread two"}


def test_repair_tolerates_task_archived_by_another_process_during_scan(tmp_path, monkeypatch) -> None:
    import os

    tasks = tmp_path / "tasks"
    archive = tmp_path / "archive"
    store = TaskStore(tasks, archive)
    created = store.create(title="Raced repair", objective="Startup repair remains idempotent.")
    directory = tasks / created.id
    terminal = created.model_copy(
        update={
            "revision": 2,
            "status": "completed",
            "updated_at": "2026-08-22T13:00:00.000000Z",
            "archived_at": "2026-08-22T13:00:00.000000Z",
        }
    )
    store._atomic_write(directory, terminal)

    original_read = store._read_dir
    moved = False

    def racing_read(path):
        nonlocal moved
        if path == directory and not moved:
            moved = True
            os.replace(directory, archive / created.id)
        return original_read(path)

    monkeypatch.setattr(store, "_read_dir", racing_read)

    assert store.repair() == []
    assert store.get(created.id) == terminal

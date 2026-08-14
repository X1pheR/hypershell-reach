from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import yaml
from pydantic import ValidationError

from hats_mcp.tasks import TaskStore


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def test_create_task_builds_task_and_evidence_directory(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    trash = tmp_path / "trash"
    store = TaskStore(tasks, trash)

    record = store.create(
        title="Deploy example",
        objective="Keep enough state to resume the deployment safely.",
        project_ref="project/example",
        next_action="Run preflight",
    )

    directory = tasks / record.id
    assert (directory / "task.yaml").is_file()
    assert (directory / "evidence").is_dir()
    assert store.get(record.id).status == "active"
    assert store.get(record.id).continuity.model_dump() == {
        "authorization": None,
        "sources": [],
        "completed": [],
        "validation": [],
        "cleanup": [],
        "recovery": None,
        "blockers": [],
        "assumptions": [],
    }


def test_task_continuity_snapshot_is_structured_bounded_and_replaceable(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks", tmp_path / "trash")
    record = store.create(
        title="Example",
        objective="Do work",
        continuity={
            "authorization": "Change only the approved example target.",
            "sources": [
                {
                    "classification": "configured",
                    "reference": "config/example.yaml",
                    "purpose": "Canonical desired state.",
                }
            ],
            "completed": ["Read current configuration."],
            "validation": ["Preflight passed."],
            "cleanup": [],
            "recovery": "Restore the previous Git revision if needed.",
            "blockers": ["Runtime activation still needs approval."],
            "assumptions": [
                {
                    "statement": "The target configuration has not changed since preflight.",
                    "evidence_class": "configured",
                    "impact_if_wrong": "high",
                    "decision": "Re-read before activation.",
                }
            ],
        },
    )

    assert record.continuity.sources[0].classification == "configured"
    assert record.continuity.assumptions[0].impact_if_wrong == "high"

    updated = store.update(
        record.id,
        continuity={
            "authorization": "Change only the approved example target.",
            "completed": ["Read current configuration.", "Applied the bounded source change."],
            "validation": ["Preflight passed.", "Changed-scope validation passed."],
            "cleanup": ["Temporary fixture removed."],
            "recovery": "Restore the previous Git revision if needed.",
            "blockers": [],
        },
    )
    assert updated.continuity.completed[-1] == "Applied the bounded source change."
    assert updated.continuity.blockers == []
    assert updated.continuity.sources == []

    with pytest.raises(ValidationError):
        store.update(
            record.id,
            continuity={"completed": [f"item-{index}" for index in range(51)]},
        )


def test_existing_task_without_continuity_reads_with_empty_snapshot(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    trash = tmp_path / "trash"
    store = TaskStore(tasks, trash)
    record = store.create(title="Legacy", objective="Read an older v1 task record")
    path = tasks / record.id / "task.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload.pop("continuity")
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    loaded = store.get(record.id)
    assert loaded.continuity.completed == []
    assert loaded.continuity.authorization is None


def test_task_update_supports_open_states_and_terminal_closure(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks", tmp_path / "trash")
    record = store.create(title="Example", objective="Do work")

    partial = store.update(record.id, status="partial", next_action="Continue")
    blocked = store.update(record.id, status="blocked", next_action="Resolve dependency")
    completed = store.update(record.id, status="completed", clear_next_action=True)

    assert partial.status == "partial"
    assert blocked.status == "blocked"
    assert completed.status == "completed"
    assert completed.next_action is None
    with pytest.raises(ValueError, match="terminal task status cannot be changed"):
        store.update(record.id, status="active")


def test_open_task_is_required_for_new_runs(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks", tmp_path / "trash")
    record = store.create(title="Example", objective="Do work")

    assert store.require_open(record.id).id == record.id
    store.update(record.id, status="completed")
    with pytest.raises(ValueError, match="task is terminal"):
        store.require_open(record.id)


def test_archive_requires_terminal_status_and_is_idempotent(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    trash = tmp_path / "trash"
    store = TaskStore(tasks, trash)
    record = store.create(title="Example", objective="Do work")

    with pytest.raises(ValueError, match="only completed or cancelled"):
        store.archive(record.id)

    store.update(record.id, status="completed")
    archived = store.archive(record.id)
    repeated = store.archive(record.id)

    assert archived.archived_at is not None
    assert repeated.id == record.id
    assert not (tasks / record.id).exists()
    assert (trash / record.id / "task.yaml").is_file()
    with pytest.raises(ValueError, match="task is archived"):
        store.require_open(record.id)


def test_list_tasks_excludes_archive_by_default(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks", tmp_path / "trash")
    active = store.create(title="Active", objective="Work")
    archived = store.create(title="Archived", objective="Work")
    store.update(archived.id, status="cancelled")
    store.archive(archived.id)

    assert [record.id for record in store.list()] == [active.id]
    assert {record.id for record in store.list(include_archived=True)} == {active.id, archived.id}
    assert [record.id for record in store.list(status="active")] == [active.id]


def test_read_only_task_store_lists_without_writing(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    trash = tmp_path / "trash"
    writable = TaskStore(tasks, trash)
    record = writable.create(title="Example", objective="Do work")
    before = (tasks / record.id / "task.yaml").read_bytes()

    read_only = TaskStore(tasks, trash, read_only=True)

    assert [item.id for item in read_only.list()] == [record.id]
    assert (tasks / record.id / "task.yaml").read_bytes() == before
    with pytest.raises(RuntimeError, match="read-only"):
        read_only.create(title="Nope", objective="No write")
    with pytest.raises(RuntimeError, match="read-only"):
        read_only.cleanup()


def test_archived_retention_removes_only_old_unretained_tasks(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    store = TaskStore(tmp_path / "tasks", tmp_path / "trash", archived_days=180, now=clock)

    old = store.create(title="Old", objective="Work")
    store.update(old.id, status="completed")
    store.archive(old.id)

    kept = store.create(title="Kept", objective="Work", retained=True)
    store.update(kept.id, status="cancelled")
    store.archive(kept.id)

    active = store.create(title="Active", objective="Work")

    clock.value += timedelta(days=181)
    removed = store.cleanup()

    assert removed == [old.id]
    assert store.get(kept.id).retained is True
    assert store.require_open(active.id).status == "active"


def test_cleanup_refuses_mismatched_task_directory(tmp_path) -> None:
    tasks = tmp_path / "tasks"
    trash = tmp_path / "trash"
    store = TaskStore(tasks, trash, archived_days=1)
    record = store.create(title="Example", objective="Work")
    store.update(record.id, status="completed")
    store.archive(record.id)

    replacement = "0" if record.id[-1] != "0" else "1"
    wrong = trash / f"{record.id[:-1]}{replacement}"
    (trash / record.id).rename(wrong)
    with pytest.raises(RuntimeError, match="task ID does not match directory"):
        store.cleanup()

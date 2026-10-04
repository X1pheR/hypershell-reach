import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest
import yaml

from hypershell_reach.database import ReachDatabase
from hypershell_reach.persistence_migration import migrate_persistence, export_legacy, inspect_database, inspect_legacy, _receipt
from hypershell_reach.runs import RunStore
from hypershell_reach.tasks import TaskStore
from test_candidates import candidate_payload


def sources(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    runs = RunStore(root / "runs")
    run = runs.create(operation="run_command", target="example", timeout_seconds=30, may_mutate=False)
    tasks = TaskStore(root / "tasks" / "active", root / "tasks" / "archive")
    task = tasks.create(title="Migration fixture", objective="Preserve production contracts", next_action="Verify")
    candidate = root / "candidates"
    candidate.mkdir()
    (candidate / "ATR-022.yaml").write_text(yaml.safe_dump(candidate_payload()))
    return dict(runs_root=root / "runs", tasks_root=tasks.tasks_root, archive_root=tasks.trash_root, candidates_root=candidate), run, task


def test_production_shaped_roundtrip_and_idempotent(tmp_path):
    inputs, run, task = sources(tmp_path)
    before = inspect_legacy(**inputs)
    db = tmp_path / "reach.sqlite3"
    receipt = migrate_persistence(db, **inputs)
    assert receipt["counts"] == {"runs": 1, "tasks": 1, "task_leases": 0, "candidates": 1}
    assert receipt["semantic_sha256"] == _receipt(before)["semantic_sha256"]
    assert migrate_persistence(db, **inputs)["already_migrated"]
    exported = tmp_path / "rollback"
    assert export_legacy(db, exported)["semantic_sha256"] == receipt["semantic_sha256"]
    db2 = tmp_path / "restored.sqlite3"
    after = migrate_persistence(db2, runs_root=exported / "runs", tasks_root=exported / "tasks/active",
                                archive_root=exported / "tasks/archive", candidates_root=exported / "candidates")
    assert after["semantic_sha256"] == receipt["semantic_sha256"]
    assert RunStore(exported / "runs", read_only=True).get(run.id).id == run.id
    assert TaskStore(exported / "tasks/active", exported / "tasks/archive", read_only=True).get(task.id).revision == task.revision
    assert inspect_legacy(**inputs) == before


def test_fresh_unconfigured_has_no_candidate_authority(tmp_path):
    db = tmp_path / "reach.sqlite3"
    receipt = migrate_persistence(db)
    assert not receipt["candidate_authority"]
    assert all(count == 0 for count in receipt["counts"].values())


@pytest.mark.parametrize("problem", ["duplicate", "id", "timestamp", "revision", "schema", "archive", "lease"])
def test_invalid_sources_fail_closed(tmp_path, problem):
    inputs, _, task = sources(tmp_path)
    path = inputs["tasks_root"] / task.id / "task.yaml"
    payload = yaml.safe_load(path.read_text())
    if problem == "duplicate":
        shutil.copytree(path.parent, inputs["archive_root"] / task.id)
    elif problem == "id":
        payload["id"] = "../other"
    elif problem == "timestamp":
        payload["created_at"] = "2026-01-01T01:00:00"
    elif problem == "revision":
        payload["revision"] = -1
    elif problem == "schema":
        payload["schema_version"] = 99
    elif problem == "archive":
        payload["archived_at"] = "2026-01-01T01:00:00Z"
    else:
        (path.parent / "execution-lease.yaml").write_text("invalid: true")
    path.write_text(yaml.safe_dump(payload))
    db = tmp_path / "reach.sqlite3"
    with pytest.raises((ValueError, RuntimeError)):
        migrate_persistence(db, **inputs)
    assert not db.exists()
    assert path.exists()


def test_unknown_destination_not_overwritten(tmp_path):
    db = tmp_path / "reach.sqlite3"
    ReachDatabase.create(db)
    with pytest.raises(RuntimeError, match="equivalent"):
        migrate_persistence(db)


def test_source_change_during_migration_not_published(tmp_path, monkeypatch):
    import hypershell_reach.persistence_migration as module
    inputs, _, _ = sources(tmp_path)
    original = module.inspect_legacy
    calls = 0
    def read(**kwargs):
        nonlocal calls
        snapshot = original(**kwargs)
        calls += 1
        if calls == 2:
            snapshot["runs"] = []
        return snapshot
    monkeypatch.setattr(module, "inspect_legacy", read)
    db = tmp_path / "reach.sqlite3"
    with pytest.raises(RuntimeError, match="source changed"):
        migrate_persistence(db, **inputs)
    assert not db.exists()
    assert not list(tmp_path.glob(".reach.sqlite3.migration-*"))


def test_exception_before_publication_is_restart_safe(tmp_path, monkeypatch):
    import hypershell_reach.persistence_migration as module
    inputs, _, _ = sources(tmp_path)
    original = module._checkpoint
    monkeypatch.setattr(module, "_checkpoint", lambda path: (_ for _ in ()).throw(RuntimeError("injected crash")))
    db = tmp_path / "reach.sqlite3"
    with pytest.raises(RuntimeError, match="injected crash"):
        migrate_persistence(db, **inputs)
    assert not db.exists()
    monkeypatch.setattr(module, "_checkpoint", original)
    assert migrate_persistence(db, **inputs)["counts"]["tasks"] == 1


def test_killed_migration_stage_is_not_authority(tmp_path):
    inputs, _, _ = sources(tmp_path)
    db = tmp_path / "reach.sqlite3"
    code = """
import os
from pathlib import Path
import hypershell_reach.persistence_migration as m
m._checkpoint = lambda path: os._exit(91)
m.migrate_persistence(Path(__import__('sys').argv[1]), **{k: Path(v) for k,v in __import__('json').loads(__import__('sys').argv[2]).items()})
"""
    result = subprocess.run([sys.executable, "-c", code, str(db), json.dumps({k: str(v) for k,v in inputs.items()})])
    assert result.returncode == 91
    assert not db.exists()
    assert list(tmp_path.glob(".reach.sqlite3.migration-*"))
    assert migrate_persistence(db, **inputs)["counts"]["runs"] == 1


def test_post_migration_writes_in_rollback(tmp_path):
    inputs, _, task = sources(tmp_path)
    db = tmp_path / "reach.sqlite3"
    migrate_persistence(db, **inputs)
    runs = RunStore(inputs["runs_root"], database=db)
    new = runs.create(operation="run_shell", target="another", timeout_seconds=30, may_mutate=False)
    tasks = TaskStore(inputs["tasks_root"], inputs["archive_root"], database=db)
    lease = tasks.acquire_execution_lease(task.id, executor_id="rollback-test", scope="Current-state proof", lease_seconds=300)
    updated = tasks.update(task.id, expected_revision=task.revision, next_action="Post-migration state", task_lease_id=lease["lease_id"])
    pending = tasks.mark_mutation_pending(task.id, purpose="Prove pending receipt survives rollback", task_lease_id=lease["lease_id"])
    from hypershell_reach.candidates import CandidateStore
    candidates = CandidateStore(inputs["candidates_root"], database=db)
    candidate = candidates.record_occurrence("ATR-022", expected_revision=1)
    destination = tmp_path / "rollback"
    export_legacy(db, destination)
    assert RunStore(destination / "runs", read_only=True).get(new.id).target == "another"
    restored_task = TaskStore(destination / "tasks/active", destination / "tasks/archive", read_only=True).get(task.id)
    assert restored_task == pending
    assert restored_task.revision == updated.revision + 1
    assert restored_task.continuity.blockers[0].startswith("[reach:pending-mutation]")
    assert yaml.safe_load((destination / "tasks/active" / task.id / "execution-lease.yaml").read_text()) == lease["owner_state"]
    assert CandidateStore(destination / "candidates", read_only=True).get("ATR-022") == candidate
    assert candidate.problem.recurrence_count == 2
    with pytest.raises(RuntimeError, match="equivalent"):
        migrate_persistence(db, **inputs)
    with pytest.raises(FileExistsError):
        export_legacy(db, destination)


def test_metadata_provenance_preserved(tmp_path):
    inputs, _, _ = sources(tmp_path)
    db = tmp_path / "reach.sqlite3"
    migrate_persistence(db, **inputs)
    snapshot = inspect_database(db)
    assert json.loads(snapshot["run_metadata"]["migration"])["source_format"] == "json"


def test_archive_retained_lease_evidence_preserved(tmp_path):
    inputs, _, task = sources(tmp_path)
    tasks = TaskStore(inputs["tasks_root"], inputs["archive_root"])
    archived = tasks.create(title="Archived", objective="History preserved")
    tasks.close(archived.id, status="completed", expected_revision=archived.revision)
    evidence = inputs["tasks_root"] / task.id / "evidence"
    evidence.mkdir()
    (evidence / "proof.txt").write_text("safe proof")
    lease = {"schema_version": 1, "lease_id": "tlease-" + "a" * 32, "executor_id": "test", "scope": "migration",
             "acquired_at": "2026-01-01T00:00:00Z", "refreshed_at": "2026-01-01T00:00:00Z", "expires_at": "2026-01-01T01:00:00Z"}
    (inputs["tasks_root"] / task.id / "execution-lease.yaml").write_text(yaml.safe_dump(lease))
    db = tmp_path / "reach.sqlite3"
    receipt = migrate_persistence(db, **inputs)
    assert receipt["task_archived"] == 1
    assert receipt["counts"]["task_leases"] == 1
    output = tmp_path / "export"
    export_legacy(db, output)
    assert (output / "tasks/active" / task.id / "evidence/proof.txt").read_text() == "safe proof"
    assert yaml.safe_load((output / "tasks/active" / task.id / "execution-lease.yaml").read_text()) == lease


@pytest.mark.parametrize("table,field,value", [
    ("runs", "status", "succeeded"), ("runs", "started_at", 0),
    ("tasks", "revision", 999), ("tasks", "blocked", 1),
    ("candidates", "recurrence_count", 99), ("candidates", "updated_at", 0),
])
def test_index_payload_disagreement_fails_closed(tmp_path, table, field, value):
    inputs, _, _ = sources(tmp_path)
    db = tmp_path / "reach.sqlite3"
    migrate_persistence(db, **inputs)
    with sqlite3.connect(db) as conn:
        conn.execute(f"UPDATE {table} SET {field}=?", (value,))
    with pytest.raises(RuntimeError, match="indexed state"):
        export_legacy(db, tmp_path / "export")


def test_legacy_run_index_corruption_rejected(tmp_path):
    inputs, _, _ = sources(tmp_path)
    with sqlite3.connect(inputs["runs_root"] / "runs.sqlite3") as conn:
        conn.execute("UPDATE runs SET status='succeeded'")
    with pytest.raises(RuntimeError, match="indexed state"):
        migrate_persistence(tmp_path / "reach.sqlite3", **inputs)


def test_export_publication_never_overwrites_racing_empty_directory(tmp_path, monkeypatch):
    import hypershell_reach.persistence_migration as module
    db = tmp_path / "reach.sqlite3"
    migrate_persistence(db)
    output = tmp_path / "rollback"
    original = module._publish_directory
    def racing_publish(stage, destination):
        destination.mkdir()
        original(stage, destination)
    monkeypatch.setattr(module, "_publish_directory", racing_publish)
    with pytest.raises(FileExistsError):
        export_legacy(db, output)
    assert output.is_dir()
    assert not list(output.iterdir())


def test_legacy_archive_without_timestamp_preserves_payload_and_never_ages_out(tmp_path):
    inputs, _, task = sources(tmp_path)
    path = inputs["tasks_root"] / task.id / "task.yaml"
    payload = yaml.safe_load(path.read_text())
    payload["schema_version"] = 1
    payload["revision"] = 0
    payload["status"] = "completed"
    payload["archived_at"] = None
    path.write_text(yaml.safe_dump(payload))
    shutil.move(str(path.parent), inputs["archive_root"] / task.id)
    db = tmp_path / "reach.sqlite3"
    receipt = migrate_persistence(db, **inputs)
    assert receipt["archive_timestamp_absent"] == [task.id]
    assert receipt["task_archived"] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE archived=1 AND archived_at < 99999999999").fetchone()[0] == 0
    output = tmp_path / "rollback"
    export_legacy(db, output)
    assert yaml.safe_load((output / "tasks/archive" / task.id / "task.yaml").read_text()) == payload



@pytest.mark.parametrize("authority", [None, "TRUE", "invalid", ""])
def test_missing_or_invalid_candidate_authority_fails_closed(tmp_path, authority):
    db = tmp_path / "reach.sqlite3"
    migrate_persistence(db)
    with sqlite3.connect(db) as conn:
        if authority is None:
            conn.execute("DELETE FROM metadata WHERE key='candidate_authority'")
        else:
            conn.execute("UPDATE metadata SET value=? WHERE key='candidate_authority'", (authority,))
    with pytest.raises(RuntimeError, match="Candidate authority"):
        export_legacy(db, tmp_path / "export")

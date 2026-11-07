"""Synthetic two-peer acceptance: import metadata, never replay execution."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError
from test_config import _config

from hypershell_reach.config import ReachConfig
from hypershell_reach.database import ReachDatabase
from hypershell_reach.peer_sync import (
    MAX_BATCH_BYTES,
    acknowledge,
    import_batch,
    imported_items,
    peer_status,
    prepare_batch,
    sync_once,
)
from hypershell_reach.runs import RunStore
from hypershell_reach.tasks import TaskStore


def peers(tmp_path):
    def config(node, primary, mode, primary_target=None):
        payload = _config()
        payload["workspace"]["database"] = str(tmp_path / node / "reach.sqlite3")
        payload["workspace"]["runs"] = str(tmp_path / node / "runs")
        payload["workspace"]["tasks"] = str(tmp_path / node / "tasks")
        payload["workspace"]["trash"] = str(tmp_path / node / "archive")
        payload["topology"] = {"node_id": node, "primary_node_id": primary,
                               "mode": mode}
        if primary_target:
            payload["topology"]["primary_target"] = primary_target
        c = ReachConfig.model_validate(payload)
        ReachDatabase.create(c.workspace.database)
        return c

    return config("oci", "home", "passive", "docker"), config("home", "home", "active")


def populate(oci):
    tasks = TaskStore(oci.workspace.tasks, oci.workspace.trash, database=oci.workspace.database)
    task = tasks.create(title="OCI recovery", objective="Restore independent site")
    runs = RunStore(oci.workspace.runs, database=oci.workspace.database)
    run = runs.create(operation="run_command", target="docker",
                      timeout_seconds=30, may_mutate=True, task_id=task.id,
                      purpose="Bounded recovery check")
    return task, run, tasks, runs


def transfer(oci, home):
    batch = prepare_batch(oci)
    receipt = import_batch(home, json.dumps(batch.model_dump()).encode(), authenticated_origin="oci")
    acknowledge(oci, batch, receipt)
    return batch


def test_initial_batch_is_origin_qualified_and_read_only(tmp_path):
    oci, home = peers(tmp_path)
    task, run, _, _ = populate(oci)
    batch = transfer(oci, home)
    assert {(x.kind, x.record_id) for x in batch.items} == {
        ("task", task.id), ("run", run.id)
    }
    assert prepare_batch(oci).items == []
    state = peer_status(home)
    assert state["imported_tasks"] == 1
    assert state["imported_runs"] == 1
    remote = imported_items(home, kind="task")
    assert remote[0]["origin"] == "oci"
    assert remote[0]["record"]["id"] == task.id
    # Imported metadata never enters the locally executable Task/Run stores.
    with pytest.raises(ValueError, match="unknown task"):
        TaskStore(home.workspace.tasks, home.workspace.trash, database=home.workspace.database).get(task.id)
    with pytest.raises(ValueError, match="unknown run"):
        RunStore(home.workspace.runs, database=home.workspace.database).get(run.id)


def test_acknowledgement_loss_retry_and_no_duplicate(tmp_path):
    oci, home = peers(tmp_path)
    populate(oci)
    batch = prepare_batch(oci)
    encoded = json.dumps(batch.model_dump()).encode()
    receipt = import_batch(home, encoded, authenticated_origin="oci")
    assert import_batch(home, encoded, authenticated_origin="oci") == receipt
    assert peer_status(home)["imported_tasks"] == 1
    assert peer_status(home)["imported_runs"] == 1
    # Source did not receive the first receipt, so resends the exact batch.
    assert prepare_batch(oci) == batch
    acknowledge(oci, batch, receipt)
    assert prepare_batch(oci).items == []


def test_changed_source_revision_and_stale_replay_rejected(tmp_path):
    oci, home = peers(tmp_path)
    task, _, tasks, _ = populate(oci)
    first = transfer(oci, home)
    tasks.update(task.id, expected_revision=task.revision, next_action="Verify recovery")
    second = prepare_batch(oci)
    assert len(second.items) == 1
    assert second.items[0].version == 2
    receipt = import_batch(home, json.dumps(second.model_dump()).encode(), authenticated_origin="oci")
    acknowledge(oci, second, receipt)
    assert imported_items(home, kind="task")[0]["version"] == 2
    with pytest.raises(ValueError, match="stale peer record"):
        import_batch(home, json.dumps(first.model_dump()).encode(), authenticated_origin="oci")


def test_source_auth_wrong_destination_and_wrong_checksum_fail_closed(tmp_path):
    oci, home = peers(tmp_path)
    populate(oci)
    batch = prepare_batch(oci)
    original = batch.model_dump()
    with pytest.raises(ValueError, match="unexpected peer identity"):
        import_batch(home, json.dumps(original).encode(), authenticated_origin="other")
    bad = json.loads(json.dumps(original))
    bad["destination"] = "elsewhere"
    with pytest.raises(ValueError, match="unexpected peer identity"):
        import_batch(home, json.dumps(bad).encode(), authenticated_origin="oci")
    bad = json.loads(json.dumps(original))
    bad["items"][0]["payload"]["title" if bad["items"][0]["kind"] == "task" else "target"] = "tampered"
    with pytest.raises(ValidationError, match="checksum"):
        import_batch(home, json.dumps(bad).encode(), authenticated_origin="oci")
    assert peer_status(home)["imported_tasks"] == 0
    assert peer_status(home)["imported_runs"] == 0


def test_same_version_new_checksum_is_conflict_not_overwrite(tmp_path):
    oci, home = peers(tmp_path)
    task, _, tasks, _ = populate(oci)
    transfer(oci, home)
    tasks.update(task.id, expected_revision=task.revision, next_action="Now changed")
    changed = prepare_batch(oci)
    # Version 2 accepted first.
    import_batch(home, json.dumps(changed.model_dump()).encode(), authenticated_origin="oci")
    # A different version-2 body must never overwrite it.
    body = changed.model_dump()
    item = body["items"][0]
    item["payload"]["objective"] = "changed via accidental fork"
    from hypershell_reach.peer_sync import _digest
    item["digest"] = _digest({"record": item["payload"], "fenced": item["fenced"]})
    with pytest.raises(ValueError, match="conflicting"):
        import_batch(home, json.dumps(body).encode(), authenticated_origin="oci")


def test_wrong_active_mode_or_oversize_or_candidate_rejected(tmp_path):
    oci, home = peers(tmp_path)
    populate(oci)
    batch = prepare_batch(oci)
    encoded = json.dumps(batch.model_dump()).encode()
    with pytest.raises(ValueError, match="active"):
        import_batch(oci, encoded, authenticated_origin="oci")
    with pytest.raises(ValueError, match="size"):
        import_batch(home, b" " * (MAX_BATCH_BYTES + 1), authenticated_origin="oci")
    payload = batch.model_dump()
    payload["items"][0]["kind"] = "candidate"
    with pytest.raises(ValidationError, match="unknown peer record kind"):
        import_batch(home, json.dumps(payload).encode(), authenticated_origin="oci")


def test_mismatched_receipt_keeps_source_pending(tmp_path):
    oci, home = peers(tmp_path)
    populate(oci)
    batch = prepare_batch(oci)
    receipt = import_batch(home, json.dumps(batch.model_dump()).encode(), authenticated_origin="oci")
    receipt["accepted"].pop()
    with pytest.raises(ValueError, match="does not match"):
        acknowledge(oci, batch, receipt)
    assert prepare_batch(oci).items == batch.items


@pytest.mark.asyncio
async def test_transport_failure_preserves_pending_records(tmp_path, monkeypatch):
    oci, home = peers(tmp_path)
    populate(oci)
    class Unreachable:
        returncode = 255
        async def communicate(self, input):
            return b"", b"host unavailable"
    async def failure(*args, **kwargs):
        return Unreachable()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", failure)
    with pytest.raises(RuntimeError, match="transport/import failed"):
        await sync_once(oci)
    assert len(prepare_batch(oci).items) == 2
    assert peer_status(oci)["pending_records"] == 2


def test_import_is_atomic_if_late_item_is_bad(tmp_path):
    oci, home = peers(tmp_path)
    populate(oci)
    batch = prepare_batch(oci).model_dump()
    item = batch["items"][1]
    item["version"] = 0
    with pytest.raises(ValidationError):
        import_batch(home, json.dumps(batch).encode(), authenticated_origin="oci")
    assert peer_status(home)["imported_tasks"] == 0
    assert peer_status(home)["imported_runs"] == 0


def test_initial_archive_is_imported_as_non_executable_history(tmp_path):
    oci, home = peers(tmp_path)
    task, _, tasks, runs = populate(oci)
    # A terminal run is not required for explicit synthetic local Task closure,
    # but unresolved pending-mutation receipts would block if one existed.
    archived = tasks.close(task.id, expected_revision=task.revision, status="completed")
    transfer(oci, home)
    rows = imported_items(home, kind="task")
    assert rows[0]["record"]["status"] == "completed"
    assert rows[0]["record"]["archived"] is True
    assert rows[0]["record"]["archived_at"] == archived.archived_at


def test_read_only_peer_routes_project_imported_metadata(tmp_path):
    from starlette.testclient import TestClient

    from hypershell_reach.ui import create_app
    oci, home = peers(tmp_path)
    populate(oci)
    transfer(oci, home)
    client = TestClient(create_app(home))
    status = client.get("/api/v1/peers")
    assert status.status_code == 200
    assert status.json()["mode"] == "active"
    assert status.json()["imported_tasks"] == 1
    assert client.get("/api/v1/peers/tasks").json()["items"][0]["origin"] == "oci"
    assert client.get("/api/v1/peers/runs").json()["items"][0]["origin"] == "oci"
    assert client.post("/api/v1/peers/tasks").status_code == 405
    page = client.get("/peers")
    assert page.status_code == 200
    assert "Read-only recovery history" in page.text
    assert "OCI recovery" in page.text


def test_import_does_not_trust_unverified_same_id_or_replay_execution(tmp_path):
    oci, home = peers(tmp_path)
    populate(oci)
    batch = prepare_batch(oci).model_dump()
    # Importing a Run is metadata-only even when it says it is running and
    # may mutate. No Home Run is queued or invoked.
    assert next(item for item in batch["items"] if item["kind"] == "run")["payload"]["may_mutate"]
    import_batch(home, json.dumps(batch).encode(), authenticated_origin="oci")
    assert peer_status(home)["imported_runs"] == 1
    with ReachDatabase(home.workspace.database).connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_passive_background_records_failures_safely(tmp_path, monkeypatch):
    import hypershell_reach.peer_sync as module
    from hypershell_reach.peer_sync import passive_sync_loop
    oci, home = peers(tmp_path)
    populate(oci)
    observed = []
    async def unreachable(_):
        raise RuntimeError("sensitive remote content should not persist")
    async def stop_after_one(_):
        observed.append(True)
        raise asyncio.CancelledError
    monkeypatch.setattr(module, "sync_once", unreachable)
    monkeypatch.setattr(module.asyncio, "sleep", stop_after_one)
    with pytest.raises(asyncio.CancelledError):
        await passive_sync_loop(oci)
    status = peer_status(oci)
    assert status["last_failure"]["category"] == "RuntimeError"
    assert "sensitive remote" not in json.dumps(status)


def test_pruning_respects_ack_and_retention(tmp_path):
    from hypershell_reach.peer_sync import prune_acknowledged
    oci, home = peers(tmp_path)
    oci.retention.runs.completed_days = 0
    oci.retention.tasks.archived_days = 0
    task, run, tasks, runs = populate(oci)
    runs.finish(run.id, {"status": "succeeded", "exit_code": 0,
                                    "timed_out": False, "duration_ms": 10})
    tasks.close(task.id, status="completed", expected_revision=task.revision)
    # No source record may be expired before verified ACK.
    assert prune_acknowledged(oci) == {"runs": 0, "tasks": 0}
    transfer(oci, home)
    assert prune_acknowledged(oci) == {"runs": 1, "tasks": 1}
    # Imported history on primary survives the source's local retention.
    assert peer_status(home)["imported_runs"] == 1
    assert peer_status(home)["imported_tasks"] == 1


def test_ambiguous_and_explicitly_retained_runs_survive_pruning(tmp_path):
    from hypershell_reach.peer_sync import prune_acknowledged
    oci, home = peers(tmp_path)
    oci.retention.runs.completed_days = 0
    task, run, tasks, runs = populate(oci)
    runs.finish(run.id, {"status": "timeout", "timed_out": True})
    transfer(oci, home)
    assert prune_acknowledged(oci)["runs"] == 0
    assert runs.get(run.id).ambiguous
    safe = runs.create(operation="run_command", target="docker", may_mutate=False,
                       timeout_seconds=10, purpose="Retained safety evidence")
    runs.finish(safe.id, {"status": "succeeded", "exit_code": 0})
    runs.set_retained(safe.id, True)
    transfer(oci, home)
    assert prune_acknowledged(oci)["runs"] == 0
    assert runs.get(safe.id).retained


def test_unacknowledged_updated_terminal_run_cannot_be_pruned(tmp_path):
    from hypershell_reach.peer_sync import prune_acknowledged
    oci, home = peers(tmp_path)
    oci.retention.runs.completed_days = 0
    task, run, tasks, runs = populate(oci)
    runs.finish(run.id, {"status": "succeeded", "exit_code": 0})
    runs.set_retained(run.id, True)
    transfer(oci, home)
    # Later retention is switched off; Home must receive that changed state.
    runs.set_retained(run.id, False)
    assert prune_acknowledged(oci)["runs"] == 0
    assert len(prepare_batch(oci).items) == 1


def test_two_phase_handoff_promotes_exactly_one_home_task(tmp_path):
    from hypershell_reach.peer_sync import fence_eligible
    oci, home = peers(tmp_path)
    task, run, source_tasks, source_runs = populate(oci)
    source_runs.finish(run.id, {"status": "succeeded", "exit_code": 0})
    transfer(oci, home)
    assert fence_eligible(oci) == [task.id]
    with pytest.raises(ValueError, match="fenced"):
        source_tasks.update(task.id, next_action="Cannot write on OCI")
    with pytest.raises(ValueError, match="fenced"):
        source_tasks.acquire_execution_lease(
            task.id, executor_id="operator", scope="continuation", lease_seconds=300)
    with pytest.raises(ValueError, match="fenced"):
        source_runs.create(
            operation="run_command", target="docker", may_mutate=False,
            timeout_seconds=20, task_id=task.id, purpose="Must not replay")
    handoff = prepare_batch(oci)
    assert len(handoff.items) == 1 and handoff.items[0].fenced
    # First response is lost. Home must keep exactly one native owner.
    receipt = import_batch(home, json.dumps(handoff.model_dump()).encode(), authenticated_origin="oci")
    first_owner = receipt["accepted"][0]["home_task_id"]
    retry_receipt = import_batch(home, json.dumps(handoff.model_dump()).encode(), authenticated_origin="oci")
    assert retry_receipt == receipt
    with ReachDatabase(home.workspace.database).connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    acknowledge(oci, handoff, retry_receipt)
    assert source_tasks.get(task.id).status == "transferred"
    assert source_tasks.get(task.id).schema_version == 3
    owner = TaskStore(home.workspace.tasks, home.workspace.trash, database=home.workspace.database)
    transferred = owner.get(first_owner)
    assert transferred.title == task.title
    assert transferred.next_action == task.next_action
    assert transferred.id != task.id
    # Home alone can now continue with its own local revision and ID.
    updated = owner.update(first_owner, expected_revision=transferred.revision,
                           next_action="Finish accepted handoff")
    assert updated.next_action == "Finish accepted handoff"
    terminal_batch = transfer(oci, home)
    assert terminal_batch.items[0].payload["status"] == "transferred"
    assert peer_status(home)["imported_tasks"] == 1
    assert TaskStore(home.workspace.tasks, home.workspace.trash,
                     database=home.workspace.database).get(first_owner).next_action == "Finish accepted handoff"


def test_fence_blocks_lease_pending_and_ambiguous_run(tmp_path):
    from hypershell_reach.peer_sync import fence_eligible
    oci, home = peers(tmp_path)
    task, run, tasks, runs = populate(oci)
    transfer(oci, home)
    # Running source Run is not eligible.
    assert fence_eligible(oci) == []
    runs.finish(run.id, {"status": "timeout", "timed_out": True})
    transfer(oci, home)
    assert fence_eligible(oci) == []  # ambiguous remote effect
    # Separate Task with an active lease is never fenced.
    second = tasks.create(title="Independent", objective="Lease protected")
    transfer(oci, home)
    lease = tasks.acquire_execution_lease(
        second.id, executor_id="operator", scope="testing", lease_seconds=300)
    assert fence_eligible(oci) == []
    tasks.release_execution_lease(second.id, lease_id=lease["lease_id"])
    tasks.mark_mutation_pending(second.id, purpose="Unreconciled old effect")
    transfer(oci, home)
    assert fence_eligible(oci) == []


def test_lost_promotion_ack_recovers_without_duplicate_owner(tmp_path):
    from hypershell_reach.peer_sync import fence_eligible
    oci, home = peers(tmp_path)
    task, run, tasks, runs = populate(oci)
    runs.finish(run.id, {"status": "succeeded", "exit_code": 0})
    transfer(oci, home)
    fence_eligible(oci)
    fenced_batch = prepare_batch(oci)
    receipt = import_batch(home, json.dumps(fenced_batch.model_dump()).encode(),
                           authenticated_origin="oci")
    # Even if Home drops before sending the receipt, no new source execution
    # is allowed and the exact import is idempotently recoverable.
    assert tasks.get(task.id).status == "active"
    with pytest.raises(ValueError, match="fenced"):
        tasks.mark_mutation_pending(task.id, purpose="No new effects")
    retry = import_batch(home, json.dumps(fenced_batch.model_dump()).encode(),
                         authenticated_origin="oci")
    assert retry == receipt
    acknowledge(oci, fenced_batch, retry)
    assert tasks.get(task.id).status == "transferred"
    with ReachDatabase(home.workspace.database).connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_identity_conflict_cannot_promote_an_unfenced_terminal_task(tmp_path):
    from hypershell_reach.peer_sync import fence_eligible
    oci, home = peers(tmp_path)
    task, run, tasks, runs = populate(oci)
    runs.finish(run.id, {"status": "succeeded", "exit_code": 0})
    transfer(oci, home)
    fence_eligible(oci)
    batch = prepare_batch(oci).model_dump()
    item = batch["items"][0]
    item["payload"]["status"] = "transferred"
    item["payload"]["schema_version"] = 3
    from hypershell_reach.peer_sync import _digest
    item["digest"] = _digest({"record": item["payload"], "fenced": True})
    with pytest.raises(ValueError, match="no ownership mapping"):
        import_batch(home, json.dumps(batch).encode(), authenticated_origin="oci")


def test_equivalent_primary_task_is_quarantined_before_fencing(tmp_path):
    from hypershell_reach.peer_sync import fence_eligible
    oci, home = peers(tmp_path)
    task, run, source_tasks, source_runs = populate(oci)
    source_runs.finish(run.id, {"status": "succeeded", "exit_code": 0})
    primary = TaskStore(home.workspace.tasks, home.workspace.trash,
                        database=home.workspace.database)
    existing = primary.create(title=task.title, objective=task.objective,
                              project_ref=task.project_ref)
    transfer(oci, home)
    assert peer_status(oci)["handoff_blocked"] == 1
    assert fence_eligible(oci) == []
    assert source_tasks.get(task.id).status == "active"
    assert primary.get(existing.id).id == existing.id
    with ReachDatabase(home.workspace.database).connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_transferred_status_cannot_be_forged_via_regular_task_mutations(tmp_path):
    from hypershell_reach.server import UpdateTaskInput
    oci, _ = peers(tmp_path)
    task, _, tasks, _ = populate(oci)
    with pytest.raises(ValidationError, match="Input should be"):
        UpdateTaskInput(task_id=task.id, status="transferred")
    with pytest.raises(ValueError, match="verified native peer handoff"):
        tasks.update(task.id, status="transferred", expected_revision=task.revision)
    with pytest.raises(ValueError, match="only completed/cancelled"):
        tasks.close(task.id, status="transferred")
    assert tasks.get(task.id).status == "active"


def test_fencing_skips_first_hundred_blocked_tasks(tmp_path):
    from datetime import UTC, datetime, timedelta

    from hypershell_reach.peer_sync import fence_eligible
    from hypershell_reach.tasks import TaskRecord, new_task_id

    oci, home = peers(tmp_path)
    ids = []
    with ReachDatabase(oci.workspace.database).connection(write=True) as conn:
        for i in range(101):
            now = (datetime(2026, 11, 7, tzinfo=UTC) + timedelta(microseconds=i))
            stamp = now.isoformat().replace("+00:00", "Z")
            record = TaskRecord(
                schema_version=2, revision=1, id=new_task_id(now),
                title=f"Distinct recovery item {i}", objective=f"Restore source item {i}",
                status="active", created_at=stamp, updated_at=stamp,
            )
            TaskStore._put(conn, record, archived=False)
            ids.append(record.id)
    for _ in range(3):
        transfer(oci, home)
    assert not prepare_batch(oci).items
    with ReachDatabase(oci.workspace.database).connection(write=True) as conn:
        for task_id in ids[:100]:
            conn.execute("INSERT INTO metadata(key,value) VALUES (?,?)", (
                f"peer/v1/handoff-blocked/task/{task_id}", '{"reason":"manual review"}'))
    assert fence_eligible(oci) == [ids[-1]]


def test_retention_scans_beyond_first_five_hundred_unacked_runs(tmp_path):
    from datetime import UTC, datetime, timedelta

    from hypershell_reach.peer_sync import _digest, prune_acknowledged
    from hypershell_reach.runs import RunRecord, RunStore, new_run_id

    oci, _ = peers(tmp_path)
    oci.retention.runs.completed_days = 0
    last = None
    with ReachDatabase(oci.workspace.database).connection(write=True) as conn:
        for i in range(501):
            now = datetime(2026, 11, 6, tzinfo=UTC) + timedelta(microseconds=i)
            stamp = now.isoformat().replace("+00:00", "Z")
            last = RunRecord(schema_version=4, id=new_run_id(now),
                             operation="run_command", target="docker",
                             timeout_seconds=5, may_mutate=False,
                             started_at=stamp, ended_at=stamp,
                             status="succeeded", ambiguous=False)
            RunStore._put(conn, last, unified=True)
        assert last is not None
        conn.execute("INSERT INTO metadata(key,value) VALUES (?,?)", (
            f"peer/v1/outbound/run/{last.id}",
            json.dumps({"version": 1, "acked_version": 1, "digest":
                        _digest({"record": last.model_dump(), "fenced": False})})))
    assert prune_acknowledged(oci) == {"runs": 1, "tasks": 0}
    with ReachDatabase(oci.workspace.database).connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 500

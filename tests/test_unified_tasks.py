from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import pytest

from hypershell_reach.database import ReachDatabase
from hypershell_reach.tasks import TaskStore, TaskContinuity


def store_at(tmp_path, **kw):
    db = ReachDatabase.create(tmp_path / "reach.sqlite3")
    return TaskStore(tmp_path / "old-active", tmp_path / "old-archive", database=db, **kw)


def test_sqlite_create_restart_query_no_old_files(tmp_path):
    s = store_at(tmp_path)
    r = s.create(title="T", objective="O", project_ref="project/test")
    assert s.create(title=" T ", objective=" O ", project_ref=" project/test ").id == r.id
    fresh = TaskStore(s.tasks_root, s.trash_root, database=s.database)
    assert fresh.get(r.id) == r
    assert fresh.query(project_ref="project/test")[0] == r
    assert fresh.count() == 1
    assert not s.tasks_root.exists() and not s.trash_root.exists()


def test_sqlite_cas_concurrent_writers(tmp_path):
    s = store_at(tmp_path)
    r = s.create(title="T", objective="O")
    def write(i):
        try:
            return s.update(r.id, expected_revision=r.revision, next_action=str(i))
        except ValueError as e:
            assert "stale task revision" in str(e)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(8)))
    assert sum(x is not None for x in results) == 1
    assert s.get(r.id).revision == 2


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_sqlite_pending_cannot_close_or_patch_away(tmp_path, status):
    s = store_at(tmp_path)
    r = s.create(title="T", objective="O")
    s.mark_mutation_pending(r.id, purpose="Test write")
    with pytest.raises(ValueError, match="pending mutation"):
        s.update(r.id, continuity={"blockers":[]})
    with pytest.raises(ValueError, match="pending mutation"):
        s.close(r.id, status=status, continuity={"blockers":[]})
    s.update(r.id, reconcile_mutation="Verified stored postcondition")
    s.close(r.id, status=status)
    assert s.count(archived=True) == 1


def test_sqlite_lease_lifecycle_no_revision_and_expiry_no_reconciliation(tmp_path):
    now = [datetime(2026, 10, 4, tzinfo=timezone.utc)]
    s = store_at(tmp_path, now=lambda:now[0])
    r = s.create(title="T", objective="O")
    a = s.acquire_execution_lease(r.id, executor_id="a", scope="test", lease_seconds=30)
    assert s.get(r.id).revision == 1
    assert not s.acquire_execution_lease(r.id, executor_id="b", scope="test", lease_seconds=30)["acquired"]
    with pytest.raises(ValueError, match="lease"):
        s.mark_mutation_pending(r.id, purpose="No lease")
    s.mark_mutation_pending(r.id, purpose="Write", task_lease_id=a["lease_id"])
    s.refresh_execution_lease(r.id, lease_id=a["lease_id"], lease_seconds=60)
    assert s.get(r.id).revision == 2
    now[0] += timedelta(seconds=61)
    b = s.acquire_execution_lease(r.id, executor_id="b", scope="test", lease_seconds=30)
    assert b["acquired"]
    s.release_execution_lease(r.id, lease_id=b["lease_id"])
    assert s.get(r.id).revision == 2
    assert s.get(r.id).continuity.blockers


def test_sqlite_archive_retention_and_open_protection(tmp_path):
    now = [datetime(2025, 1, 1, tzinfo=timezone.utc)]
    s = store_at(tmp_path, now=lambda:now[0], archived_days=180)
    old = s.create(title="old", objective="O")
    retained = s.create(title="retain", objective="O", retained=True)
    active = s.create(title="active", objective="O")
    s.close(old.id, status="completed")
    s.close(retained.id, status="cancelled")
    now[0] += timedelta(days=181)
    assert s.cleanup() == [old.id]
    assert s.get(retained.id).retained and s.get(active.id).status == "active"
    assert s.count(archived=True) == 1


def test_sqlite_malformed_record_fails_closed(tmp_path):
    s = store_at(tmp_path)
    r = s.create(title="T", objective="O")
    with s.database.connection(write=True) as conn:
        conn.execute("UPDATE tasks SET payload='{}' WHERE id=?", (r.id,))
    with pytest.raises(RuntimeError, match="invalid task"):
        s.get(r.id)


def test_sqlite_read_only_no_mutation(tmp_path):
    s = store_at(tmp_path)
    r = s.create(title="T", objective="O")
    ro = TaskStore(s.tasks_root, s.trash_root, database=s.database, read_only=True)
    assert ro.get(r.id) == r
    with pytest.raises(RuntimeError, match="read-only"):
        ro.update(r.id, next_action="no")


def test_sqlite_query_search_sort_pagination_counts(tmp_path):
    s = store_at(tmp_path)
    a = s.create(title="A_100%", objective="O", next_action="Inspect source")
    b = s.create(title="Beta", objective="O", continuity={"blockers":["blocked"]})
    assert s.query(q="_100%") == [a]
    assert s.count(q="_100%") == 1
    assert s.query(q="Inspect") == [a]
    assert s.query(sort="title", descending=False, limit=1, offset=1) == [b]
    assert s.count(blocked=True) == 1
    assert s.query(blocked=True) == [b]
    with pytest.raises(ValueError, match="sort"):
        s.query(sort="payload;DROP TABLE tasks")


def test_sqlite_concurrent_equivalent_create_and_lease(tmp_path):
    s = store_at(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _:s.create(title="Same",objective="O"),range(8)))
    assert len({r.id for r in results}) == 1 and s.count() == 1
    r = results[0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = list(pool.map(lambda i:s.acquire_execution_lease(r.id,executor_id=str(i),scope="test",lease_seconds=30),range(8)))
    assert sum(x["acquired"] for x in leases) == 1
    assert s.get(r.id).revision == 1
    summary = s.lease_summary(r.id)
    assert summary and "lease_id" not in summary


def test_sqlite_archive_failure_is_atomic_and_repair_preserves_revision(tmp_path,monkeypatch):
    s = store_at(tmp_path)
    r = s.create(title="T",objective="O")
    original = s._move_to_archive
    def fail(*_):
        raise RuntimeError("injected failure")
    monkeypatch.setattr(s,"_move_to_archive",fail)
    with pytest.raises(RuntimeError,match="injected"):
        s.close(r.id,status="completed")
    assert s.get(r.id) == r and s.count(archived=True) == 0
    monkeypatch.setattr(s,"_move_to_archive",original)
    committed = r.model_copy(update={"status":"completed","archived_at":r.updated_at})
    with s.database.connection(write=True) as conn:
        s._put(conn,committed,archived=False)
    assert s.repair() == [r.id]
    assert s.get(r.id) == committed
    assert s.repair() == []


def test_sqlite_task_terminal_rules_and_no_stale_file_authority(tmp_path):
    s = store_at(tmp_path)
    r = s.create(title="T",objective="O",next_action="work")
    with pytest.raises(ValueError,match="next_action"):
        s.close(r.id,status="completed")
    final = s.close(r.id,status="completed",clear_next_action=True)
    assert s.close(r.id,status="completed") == final
    with pytest.raises(ValueError,match="status cannot"):
        s.update(r.id,status="active")
    s.tasks_root.mkdir()
    (s.tasks_root/"malformed-retired-file.yaml").write_text("ignored")
    assert s.get(r.id) == final
    assert s.list(include_archived=True) == [final]


def test_sqlite_cancel_cannot_introduce_pending_receipt(tmp_path):
    s = store_at(tmp_path)
    r = s.create(title="T",objective="O")
    with pytest.raises(ValueError,match="pending mutation"):
        s.close(r.id,status="cancelled",continuity={"blockers":["[reach:pending-mutation] unresolved"]})
    assert s.get(r.id) == r


def test_sqlite_archived_null_timestamp_preserved_and_not_retained_by_age(tmp_path):
    s = store_at(tmp_path,archived_days=180)
    r = s.create(title="Historical",objective="O")
    historical = r.model_copy(update={"status":"completed"})
    with s.database.connection(write=True) as conn:
        s._put(conn,historical,archived=True)
    assert s.repair() == []
    assert s.get(r.id) == historical
    assert s.query(archived=True) == [historical]
    assert s.count(archived=True) == 1
    assert s.cleanup() == []
    assert s.get(r.id).archived_at is None


@pytest.mark.parametrize("status", ["completed","cancelled"])
def test_sqlite_archived_pending_cannot_be_acknowledged_closed(tmp_path,status):
    s = store_at(tmp_path)
    r = s.create(title="Historical",objective="O")
    pending = r.model_copy(update={"status":status,"continuity":TaskContinuity(blockers=["[reach:pending-mutation] unresolved"])})
    with s.database.connection(write=True) as conn:
        s._put(conn,pending,archived=True)
    with pytest.raises(ValueError,match="pending mutation"):
        s.close(r.id,status=status)


@pytest.mark.parametrize("field,value", [
    ("id", "task-20200101T000000000000Z-abcdef123456"),
    ("retained", True),
    ("status", "active"),
    ("archived_at", "2026-10-04T00:00:00Z"),
])
def test_sqlite_cleanup_malformed_payload_disagreement_fails_closed(tmp_path,field,value):
    import json
    now = [datetime(2025,1,1,tzinfo=timezone.utc)]
    s = store_at(tmp_path,now=lambda:now[0],archived_days=180)
    a = s.create(title="valid",objective="O")
    b = s.create(title="corrupt",objective="O")
    s.close(a.id,status="completed")
    final = s.close(b.id,status="completed")
    payload = final.model_dump(mode="json")
    payload[field] = value
    with s.database.connection(write=True) as conn:
        conn.execute("UPDATE tasks SET payload=? WHERE id=?",(json.dumps(payload),b.id))
    now[0] += timedelta(days=181)
    with pytest.raises(RuntimeError,match="cleanup index"):
        s.cleanup()
    assert s.count(archived=True) == 2


def test_sqlite_equivalent_create_malformed_identity_fails_closed(tmp_path):
    import json
    s = store_at(tmp_path)
    r = s.create(title="T",objective="O")
    payload = r.model_dump(mode="json")
    payload["id"] = "task-20200101T000000000000Z-abcdef123456"
    with s.database.connection(write=True) as conn:
        conn.execute("UPDATE tasks SET payload=? WHERE id=?",(json.dumps(payload),r.id))
    with pytest.raises(RuntimeError,match="identity"):
        s.create(title="T",objective="O")

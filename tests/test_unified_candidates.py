from concurrent.futures import ThreadPoolExecutor
import pytest
from hypershell_reach.database import ReachDatabase
from hypershell_reach.candidates import CandidateStore, CandidateRecord, CandidateReference
from test_candidates import candidate_payload


def setup(tmp_path):
    db = ReachDatabase.create(tmp_path / "reach.sqlite3")
    with db.connection(write=True) as conn:
        conn.execute("INSERT INTO metadata(key,value) VALUES('candidate_authority','true')")
    s = CandidateStore(tmp_path / "old-candidates", database=db)
    return s


def create(s, candidate_id=None):
    r = CandidateRecord.model_validate(candidate_payload())
    return s.create(candidate_id=candidate_id, title=r.title, problem=r.problem,
                    proposal=r.proposal, ownership=r.ownership,
                    promotion_rationale=r.promotion.rationale)


def test_sqlite_candidate_lifecycle_restart_and_no_old_files(tmp_path):
    s = setup(tmp_path)
    r = create(s)
    assert r.id.startswith("CAN-")
    r = s.record_occurrence(r.id, expected_revision=1)
    assert r.problem.recurrence_count == 2
    r = s.transition(r.id, expected_revision=2, target_state="approved", state_reason="Authorized")
    r = s.transition(r.id, expected_revision=3, target_state="implemented", state_reason="Proven",
                     final_reference=CandidateReference(kind="capability", id="reach.query"))
    fresh = CandidateStore(s.root, database=s.database)
    assert fresh.get(r.id) == r
    assert fresh.count(state="implemented") == 1
    assert fresh.query(owner_id=r.ownership.owner_id) == [r]
    assert not s.root.exists()


def test_sqlite_candidate_explicit_id_collision_and_cas(tmp_path):
    s = setup(tmp_path)
    r = create(s, "ATR-022")
    with pytest.raises(ValueError, match="already exists"):
        create(s, "ATR-022")
    def mutate(_):
        try:
            return s.record_occurrence(r.id, expected_revision=1)
        except ValueError as e:
            assert "stale candidate revision" in str(e)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(mutate, range(8)))
    assert sum(x is not None for x in results) == 1
    assert s.get(r.id).problem.recurrence_count == 2


def test_sqlite_candidate_unconfigured_fails_closed(tmp_path):
    s = setup(tmp_path)
    create(s)
    with pytest.raises(ValueError, match="not configured"):
        CandidateStore(None, database=s.database)


def test_sqlite_candidate_read_only_and_malformed(tmp_path):
    s = setup(tmp_path)
    r = create(s)
    ro = CandidateStore(s.root, database=s.database, read_only=True)
    assert ro.get(r.id) == r
    with pytest.raises(RuntimeError, match="read-only"):
        ro.record_occurrence(r.id, expected_revision=1)
    with s.database.connection(write=True) as conn:
        conn.execute("UPDATE candidates SET payload='{}' WHERE id=?", (r.id,))
    with pytest.raises(RuntimeError, match="invalid candidate"):
        s.get(r.id)


def test_sqlite_candidate_query_search_sort_pagination(tmp_path):
    s = setup(tmp_path)
    a = create(s,"AA-001")
    b = create(s,"BB-001")
    assert s.query(q="AA-001") == [a]
    assert s.count(q="AA-001") == 1
    assert s.query(q="recurring") == s.list()
    assert s.query(sort="id",descending=False,limit=1,offset=1) == [b]
    with pytest.raises(ValueError,match="sort"):
        s.query(sort="payload; DROP TABLE candidates")


def test_sqlite_candidate_update_lifecycle_and_duplicate_id_constraint(tmp_path):
    import sqlite3
    s = setup(tmp_path)
    r = create(s)
    r = s.update(r.id,expected_revision=1,title="Revised")
    r = s.transition(r.id,expected_revision=2,target_state="blocked",state_reason="Missing evidence")
    r = s.transition(r.id,expected_revision=3,target_state="approved",state_reason="Evidence arrived")
    task_id = "task-20261004T100000000000Z-abcdef123456"
    r = s.link_task(r.id,expected_revision=4,task_id=task_id)
    assert r.implementation.task_id == task_id
    assert s.link_task(r.id,expected_revision=5,task_id=task_id) == r
    r = s.transition(r.id,expected_revision=5,target_state="not-warranted",state_reason="Superseded")
    with pytest.raises(ValueError,match="invalid candidate transition"):
        s.transition(r.id,expected_revision=6,target_state="approved",state_reason="No replay")
    with s.database.connection(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO candidates SELECT * FROM candidates WHERE id=?",(r.id,))


@pytest.mark.parametrize("authority", [None, "false"])
def test_sqlite_candidate_disabled_database_cannot_be_enabled_by_root(tmp_path,authority):
    db = ReachDatabase.create(tmp_path / "reach.sqlite3")
    if authority is not None:
        with db.connection(write=True) as conn:
            conn.execute("INSERT INTO metadata(key,value) VALUES('candidate_authority',?)",(authority,))
    with pytest.raises(ValueError,match="not configured"):
        CandidateStore(tmp_path / "accidental-root",database=db)
    assert not (tmp_path / "accidental-root").exists()

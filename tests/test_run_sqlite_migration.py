from datetime import datetime,timezone
import json,sqlite3
from pathlib import Path
import pytest
from hypershell_reach.runs import RunStore,RunRecord

def legacy(root, suffix="a", mode="sync", status="running"):
    record=RunRecord(id="run-20261003T120000000000Z-"+suffix*12,operation="run_command",target="example",timeout_seconds=30,may_mutate=True,execution_mode=mode,status=status,started_at="2026-10-03T12:00:00Z",schema_version=4)
    path=root/(record.id+".json");path.write_text(RunStore._serialize(record));return record,path

def test_transactional_migration_preserves_preimage_and_owner_reconciliation(tmp_path):
    sync,sp=legacy(tmp_path);async_run,ap=legacy(tmp_path,"b","async")
    before={p.name:p.read_bytes() for p in [sp,ap]}
    store=RunStore(tmp_path,reconcile_modes={"sync"})
    assert store.get(sync.id).error_type=="ServerRestart"
    assert store.get(async_run.id).status=="running"
    assert all(p.read_bytes()==before[p.name] for p in [sp,ap])
    assert RunStore(tmp_path,reconcile_modes={"async"}).get(async_run.id).error_type=="ExecutorRestart"
    with sqlite3.connect(store.database_path) as db:
        receipt=json.loads(db.execute("SELECT value FROM metadata WHERE key='migration'").fetchone()[0])
    assert receipt["source_records"]==2 and len(receipt["source_sha256"])==64
    assert store.database_path.stat().st_mode & 0o777 == 0o600

def test_bad_migration_never_accepts_partial_database(tmp_path):
    record,path=legacy(tmp_path)
    bad=tmp_path/"run-20261003T120000000000Z-bbbbbbbbbbbb.json";bad.write_text("{")
    with pytest.raises(RuntimeError,match="invalid run"):RunStore(tmp_path)
    assert not (tmp_path/"runs.sqlite3").exists()
    assert path.exists() and bad.read_text()=="{"
    bad.unlink();assert RunStore(tmp_path,reconcile_modes=set()).count()==1

def test_read_only_legacy_does_not_migrate_and_missing_root_stays_missing(tmp_path):
    record,path=legacy(tmp_path)
    store=RunStore(tmp_path,read_only=True)
    assert store.get(record.id).status=="running" and store.count()==1
    assert not (tmp_path/"runs.sqlite3").exists()
    missing=tmp_path/"absent";assert RunStore(missing,read_only=True).count()==0
    assert not missing.exists()

def test_restart_never_rescans_or_resurrects_retired_legacy_records(tmp_path,monkeypatch):
    record,path=legacy(tmp_path,status="succeeded")
    store=RunStore(tmp_path,reconcile_modes=set())
    with store._database() as db:db.execute("DELETE FROM runs")
    monkeypatch.setattr(Path,"glob",lambda *a,**k: (_ for _ in ()).throw(AssertionError("legacy rescan")))
    reopened=RunStore(tmp_path,reconcile_modes=set())
    assert reopened.count()==0 and reopened.list()==[]

def test_export_includes_postmigration_changes_for_rollback(tmp_path):
    root=tmp_path/"source";root.mkdir();old,path=legacy(root)
    store=RunStore(root,reconcile_modes=set());store.set_retained(old.id,True)
    new=store.create(operation="run_command",target="example",timeout_seconds=30,may_mutate=False,result_ref="reports/result.json")
    output=tmp_path/"rollback";receipt=RunStore(root,read_only=True).export_json(output)
    assert receipt["records"]==2
    assert json.loads((output/(old.id+".json")).read_text())["retained"] is True
    restored=RunStore(output,read_only=True);assert restored.get(new.id).result_ref=="reports/result.json"
    with pytest.raises(FileExistsError):store.export_json(output)

def test_corrupt_or_future_database_fails_closed(tmp_path):
    store=RunStore(tmp_path)
    with sqlite3.connect(store.database_path) as db:db.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError,match="unsupported"):RunStore(tmp_path)


def test_read_model_created_before_lifespan_sees_new_runs(tmp_path):
    observer=RunStore(tmp_path,read_only=True)
    writer=RunStore(tmp_path,reconcile_modes=set())
    record=writer.create(operation="run_command",target="example",timeout_seconds=30,may_mutate=False)
    assert observer.count()==1 and observer.get(record.id).status=="running"
    writer.database_path.unlink()
    with pytest.raises(sqlite3.OperationalError):observer.count()


def test_interrupted_migration_keeps_preimage_and_can_retry(tmp_path,monkeypatch):
    legacy(tmp_path);legacy(tmp_path,"b")
    original=RunStore._put;calls=0
    def crash(connection,record):
        nonlocal calls
        calls+=1
        if calls==2:raise KeyboardInterrupt()
        original(connection,record)
    with monkeypatch.context() as m:
        m.setattr(RunStore,"_put",staticmethod(crash))
        with pytest.raises(KeyboardInterrupt):RunStore(tmp_path)
    assert not (tmp_path/"runs.sqlite3").exists()
    assert len(list(tmp_path.glob("run-*.json")))==2
    assert RunStore(tmp_path,reconcile_modes=set()).count()==2

def test_concurrent_initialization_has_one_complete_migration(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    legacy(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        counts=list(pool.map(lambda _:RunStore(tmp_path,reconcile_modes=set()).count(),range(3)))
    assert counts==[1,1,1]

def test_legacy_symlink_and_filename_mismatch_fail_closed(tmp_path):
    record,path=legacy(tmp_path)
    path.rename(tmp_path/(record.id.replace('aaaaaaaaaaaa','bbbbbbbbbbbb')+'.json'))
    with pytest.raises(RuntimeError,match='identity mismatch'):RunStore(tmp_path)
    assert not (tmp_path/'runs.sqlite3').exists()


def test_cli_rollback_export_uses_configured_peer_store(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    from hypershell_reach import service
    root=tmp_path/'source';store=RunStore(root,reconcile_modes=set())
    record=store.create(operation='run_command',target='example',timeout_seconds=30,may_mutate=False)
    monkeypatch.setattr(service,'load_config',lambda _:SimpleNamespace(workspace=SimpleNamespace(runs=root)))
    output=tmp_path/'export';service.main(['export-runs-json','--output',str(output)])
    assert json.loads(capsys.readouterr().out)['records']==1
    assert (output/(record.id+'.json')).exists()

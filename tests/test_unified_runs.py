from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import sqlite3
import pytest
from hypershell_reach.database import ReachDatabase
from hypershell_reach.runs import RunStore


def store(tmp_path, **kwargs):
    db = ReachDatabase.create(tmp_path / 'reach.sqlite3')
    return RunStore(tmp_path / 'legacy-runs', database=db, reconcile_modes=set(), **kwargs)


def create(runs, **kwargs):
    return runs.create(operation='run_command', target='host', timeout_seconds=30, may_mutate=False, purpose='Validate storage', **kwargs)


def finish(runs, run_id):
    return runs.finish(run_id, {'status':'succeeded','exit_code':0,'duration_ms':1})


def test_unified_runs_preserve_payload_and_do_not_write_legacy(tmp_path):
    runs = store(tmp_path)
    record = create(runs, task_id='example-task', execution_class='heavy')
    finish(runs, record.id)
    actual = runs.get(record.id)
    assert actual.status == 'succeeded'
    assert actual.task_id == 'example-task'
    assert actual.execution_class == 'heavy'
    assert not (tmp_path / 'legacy-runs').exists()
    assert runs.count(task_id='example-task') == 1
    assert runs.list(status='succeeded')[0] == actual
    ro = RunStore(runs.root, database=runs.database_path, read_only=True)
    assert ro.get(record.id) == actual
    with pytest.raises(RuntimeError, match='read-only'):
        ro.set_retained(record.id, True)


def test_unified_query_paging_filters_and_counts(tmp_path):
    now = datetime(2026,1,1,tzinfo=timezone.utc)
    runs = store(tmp_path, now=lambda: now)
    ids = []
    for i in range(6):
        now += timedelta(seconds=1)
        record = create(runs, task_id='one' if i % 2 else 'two')
        ids.append(record.id)
        finish(runs,record.id)
    runs.set_retained(ids[0], True)
    assert runs.count(status='succeeded', task_id='one') == 3
    assert runs.count(retained=True) == 1
    assert runs.count(started_after='2026-01-01T00:00:04Z') == 3
    assert len(runs.list(target='host',operation='run_command',execution_mode='sync',execution_class='normal')) == 6
    assert [r.id for r in runs.query(limit=2,offset=2)] == list(reversed(ids))[2:4]
    assert runs.count(q='storage') == 6
    assert runs.count(q='NOT PRESENT') == 0
    with pytest.raises(ValueError):
        runs.list(sort='payload;DROP TABLE runs')
    with runs._database() as conn:
        plans = str([tuple(row) for row in conn.execute("EXPLAIN QUERY PLAN SELECT payload FROM runs WHERE task_id='one' ORDER BY id DESC LIMIT 2")])
        assert 'INDEX runs_task_id' in plans


def test_unified_retention_guards_and_owner_reconciliation(tmp_path):
    now = datetime(2026,1,1,tzinfo=timezone.utc)
    runs = store(tmp_path, now=lambda: now, completed_days=30)
    normal = create(runs); finish(runs, normal.id)
    retained = create(runs); finish(runs, retained.id); runs.set_retained(retained.id, True)
    sync = create(runs)
    asynchronous = create(runs, execution_mode='async')
    now += timedelta(days=31)
    sync_owner = RunStore(runs.root,database=runs.database_path,reconcile_modes={'sync'},now=lambda:now)
    assert sync_owner.get(sync.id).error_type == 'ServerRestart'
    assert sync_owner.get(asynchronous.id).status == 'running'
    async_owner = RunStore(runs.root,database=runs.database_path,reconcile_modes={'async'},now=lambda:now)
    assert async_owner.get(asynchronous.id).error_type == 'ExecutorRestart'
    assert runs.cleanup() == [normal.id]
    assert runs.get(retained.id).retained
    assert runs.count() == 3


def test_concurrent_insert_finish_and_cas_like_finalization(tmp_path):
    runs = store(tmp_path)
    def execute(_):
        record = create(runs)
        finish(runs,record.id)
        return record.id
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(execute,range(80)))
    assert len(set(ids)) == 80
    assert runs.count(status='succeeded') == 80
    with pytest.raises(RuntimeError, match='not running'):
        finish(runs,ids[0])
    with runs._database() as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_ambiguous_terminal_retention_protected(tmp_path):
    now = datetime(2026,1,1,tzinfo=timezone.utc)
    runs = store(tmp_path, now=lambda:now, completed_days=30)
    record = runs.create(operation='run_command',target='host',timeout_seconds=30,
                         may_mutate=True,purpose='Validate uncertain mutation')
    runs.finish(record.id, {'status':'transport_error','exit_code':None})
    now += timedelta(days=31)
    assert runs.cleanup() == []
    assert runs.get(record.id).ambiguous


@pytest.mark.asyncio
async def test_unified_async_await_and_authoritative_owner_loss(tmp_path, monkeypatch):
    import asyncio
    from hypershell_reach import executor
    from test_executor import _config, _submission
    config = _config(tmp_path)
    db = ReachDatabase.create(tmp_path / 'reach.sqlite3')
    config.workspace.database = str(db.path)
    release = asyncio.Event()
    calls = []
    async def fake_run_ssh(**kwargs):
        calls.append(kwargs)
        await release.wait()
        return {'target':kwargs['target_id'],'status':'succeeded','exit_code':0,
                'stdout':{'text':'SECRET-NOT-PERSISTED','bytes':20,'truncated':False},
                'stderr':{'text':'','bytes':0,'truncated':False}}
    monkeypatch.setattr(executor,'run_ssh',fake_run_ssh)
    service = executor.ExecutorService(config,serve_socket=False)
    await service.start()
    try:
        accepted = service.submit(_submission())
        waiter = asyncio.create_task(service.await_terminal(accepted['run_id'],max_wait_seconds=2))
        await asyncio.sleep(0)
        assert not waiter.done()
        release.set()
        result = await waiter
        assert result['terminal'] and result['status'] == 'succeeded'
        assert len(calls) == 1
        assert 'SECRET-NOT-PERSISTED' not in service.store.get(accepted['run_id']).model_dump_json()
        orphan = service.store.create(operation='run_command',target='example',timeout_seconds=300,
                                      may_mutate=True,execution_mode='async',purpose='Validate owner evidence')
        recovered = await service.await_terminal(orphan.id,max_wait_seconds=1)
        assert recovered['reconciliation']['reason'] == 'ExecutorOwnershipLost'
        assert service.store.get(orphan.id).ambiguous
        legitimate = service.store.create(operation='run_command',target='example',timeout_seconds=300,
                                          may_mutate=False,purpose='Observe a current synchronous owner')
        observed = await service.await_terminal(legitimate.id,max_wait_seconds=0)
        assert observed['terminal'] is False and observed['status'] == 'running'
        assert observed['reconciliation']['reason'] == 'NoAuthoritativeOwnerEvidence'
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_unified_sync_owner_loss_preserves_server_semantics(tmp_path,monkeypatch):
    from hypershell_reach import server
    from test_server_runs import _config
    config = _config(tmp_path)
    runs = store(tmp_path)
    monkeypatch.setattr(server,'_run_store_instance',runs)
    class OwnerLost(BaseException):
        pass
    async def fake_run_ssh(**kwargs):
        raise OwnerLost
    monkeypatch.setattr(server,'run_ssh',fake_run_ssh)
    with pytest.raises(OwnerLost):
        await server._tracked_ssh_run(operation='run_command',target_id='example',
              target=config.enabled_target('example'),remote_command='true',timeout_seconds=30,
              connect_timeout_seconds=10,max_output_bytes=262144,may_mutate=True,
              purpose='Validate synchronous owner loss')
    record = runs.list()[0]
    assert record.status == 'unknown' and record.error_type == 'ServerOwnershipLost'
    assert record.ambiguous

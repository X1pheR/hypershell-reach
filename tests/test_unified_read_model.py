from starlette.testclient import TestClient
import pytest
from hypershell_reach.database import ReachDatabase
from hypershell_reach.runs import RunStore
from hypershell_reach.tasks import TaskStore
from hypershell_reach.candidates import CandidateStore
from hypershell_reach.read_model import ReachReadModel
from hypershell_reach.ui import create_app
from test_ui import _config
from test_candidates import create_candidate

def setup_store(tmp_path, *, candidates=False):
    config = _config(tmp_path)
    config.workspace.database = str(tmp_path / 'reach.sqlite3')
    if candidates:
        config.workspace.candidates = str(tmp_path / 'candidates')
    db = ReachDatabase.create(config.workspace.database)
    with db.connection(write=True) as conn:
        conn.execute("INSERT INTO metadata(key,value) VALUES ('candidate_authority',?)", ('true' if candidates else 'false',))
    tasks = TaskStore(config.workspace.tasks, config.workspace.trash, database=db)
    runs = RunStore(config.workspace.runs, database=db)
    return config, tasks, runs

def test_counts_not_limited_and_lists_safe(tmp_path):
    config, tasks, runs = setup_store(tmp_path)
    for index in range(505):
        tasks.create(title=f'Task {index:04d}', objective='Private objective', project_ref='project')
    client = TestClient(create_app(config))
    summary = client.get('/api/v1/summary').json()
    assert summary['counts']['tasks'] == 505
    assert summary['counts']['active_tasks'] == 505
    page = client.get('/api/v1/tasks?limit=5&offset=500&sort=title&dir=asc').json()
    assert page['count'] == 5 and page['total'] == 505
    assert page['items'][0]['title'] == 'Task 0500'
    assert all('objective' not in item and 'continuity' not in item for item in page['items'])
    html = client.get('/tasks?page=21&sort=title&dir=asc')
    assert html.status_code == 200
    assert '505 results · showing 501–505' in html.text
    assert 'Task 0504' in html.text
    assert 'Private objective' not in html.text
    assert client.get('/api/v1/tasks?q=Task%200504').json()['total'] == 1
    assert client.get('/api/v1/tasks?q=Private%20objective').json()['total'] == 0

def test_task_runs_filtering_and_ui_past_first_hundred(tmp_path):
    config, tasks, runs = setup_store(tmp_path)
    task = tasks.create(title='Linked task', objective='Bounded check')
    other = tasks.create(title='Other task', objective='Other check')
    for index in range(105):
        runs.create(operation='run_command', target='docker', timeout_seconds=30,
                    may_mutate=False, task_id=task.id if index == 0 else other.id,
                    purpose=f'Inspect item {index:03d}')
    client = TestClient(create_app(config))
    page = client.get(f'/api/v1/runs?task_id={task.id}&status=running').json()
    assert page['total'] == page['count'] == 1
    assert page['items'][0]['task_id'] == task.id
    assert client.get('/api/v1/runs?limit=5&offset=100&dir=asc').json()['count'] == 5
    assert '105 results · showing 101–105' in client.get('/runs?page=5').text
    assert client.get('/api/v1/runs?q=Inspect%20item%20000').json()['total'] == 1
    assert len(ReachReadModel(config).related_run_summaries(task.id)) == 1

@pytest.mark.parametrize('query', ['limit=0', 'limit=501', 'offset=-1', 'offset=x', 'sort=payload', 'dir=sideways', 'retained=1', 'status=invalid'])
@pytest.mark.parametrize('kind', ['runs', 'tasks'])
def test_invalid_queries(tmp_path, query, kind):
    config, _, _ = setup_store(tmp_path)
    response = TestClient(create_app(config)).get(f'/api/v1/{kind}?{query}')
    assert response.status_code == 400
    assert 'sqlite' not in response.text.lower()

def test_candidates_read_only_and_unconfigured_absent(tmp_path):
    config, _, _ = setup_store(tmp_path, candidates=True)
    store = CandidateStore(config.workspace.candidates, database=config.workspace.database)
    create_candidate(store, 'ATR-001')
    create_candidate(store, 'ATR-002')
    client = TestClient(create_app(config))
    page = client.get('/api/v1/candidates?limit=1&offset=1&sort=id&dir=asc').json()
    assert page['count'] == 1 and page['total'] == 2
    assert page['items'][0]['id'] == 'ATR-002'
    assert 'proposal' not in page['items'][0]
    assert client.post('/api/v1/candidates').status_code == 405
    config.workspace.candidates = None
    denied = TestClient(create_app(config)).get('/api/v1/candidates').json()
    assert denied == {'count': 0, 'total': 0, 'items': [], 'configured': False}


def test_archived_queries_and_counts(tmp_path):
    config, tasks, _ = setup_store(tmp_path)
    task = tasks.create(title="Closed history", objective="Finished")
    tasks.close(task.id, status="completed", expected_revision=task.revision)
    client = TestClient(create_app(config))
    assert client.get('/api/v1/tasks').json()['total'] == 0
    archived = client.get('/api/v1/tasks?archived=true').json()
    assert archived['total'] == 1
    assert archived['items'][0]['archived'] is True
    assert client.get('/api/v1/tasks?archived=all').json()['total'] == 1
    assert client.get('/api/v1/summary').json()['counts']['archived_tasks'] == 1


def test_historical_archive_location_is_projected_without_inventing_timestamp(tmp_path):
    import json
    config, tasks, _ = setup_store(tmp_path)
    task = tasks.create(title="Historical archive", objective="Preserve physical truth")
    closed = tasks.close(task.id, status="completed", expected_revision=task.revision)
    payload = closed.model_dump()
    payload["archived_at"] = None
    with tasks.database.connection(write=True) as conn:
        conn.execute("UPDATE tasks SET archived_at=NULL,payload=? WHERE id=?",
                     (json.dumps(payload), task.id))
    client = TestClient(create_app(config))
    page = client.get('/api/v1/tasks?archived=true').json()
    assert page['total'] == 1
    assert page['items'][0]['archived'] is True
    assert page['items'][0]['archived_at'] is None
    assert tasks.get(task.id).model_dump() == payload
    tasks.archived_days = 0
    assert tasks.cleanup() == []
    with tasks.database.connection() as conn:
        stored = conn.execute("SELECT payload,archived_at,archived FROM tasks WHERE id=?", (task.id,)).fetchone()
    assert json.loads(stored['payload']) == payload
    assert stored['archived_at'] is None and stored['archived'] == 1

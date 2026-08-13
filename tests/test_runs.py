from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from hats_mcp.runs import RunStore


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _execution(status: str = "succeeded") -> dict[str, object]:
    return {
        "target": "example",
        "status": status,
        "exit_code": 0 if status == "succeeded" else 255,
        "timed_out": status == "timeout",
        "duration_ms": 20,
        "stdout": {"text": "secret output is not persisted", "bytes": 30, "truncated": False},
        "stderr": {"text": "secret error is not persisted", "bytes": 29, "truncated": False},
    }


def test_run_record_excludes_execution_content(tmp_path) -> None:
    store = RunStore(tmp_path)
    record = store.create(
        operation="run_command",
        target="example",
        timeout_seconds=30,
        may_mutate=True,
    )
    finished = store.finish(record.id, _execution())
    payload = (tmp_path / f"{record.id}.json").read_text(encoding="utf-8")

    assert finished.status == "succeeded"
    assert finished.stdout_bytes == 30
    assert "secret output" not in payload
    assert "secret error" not in payload
    assert '"operation": "run_command"' in payload
    assert finished.idempotent is None


def test_managed_run_persists_declared_idempotency(tmp_path) -> None:
    store = RunStore(tmp_path)
    record = store.create(
        operation="run_script",
        target="example",
        timeout_seconds=30,
        may_mutate=True,
        idempotent=False,
        script_id="system.example",
    )
    finished = store.finish(record.id, _execution())

    assert finished.idempotent is False
    assert finished.summary()["may_mutate"] is True
    assert finished.summary()["idempotent"] is False


def test_existing_run_without_idempotent_reads_as_unknown(tmp_path) -> None:
    store = RunStore(tmp_path)
    record = store.create(
        operation="run_command",
        target="example",
        timeout_seconds=30,
        may_mutate=True,
    )
    path = tmp_path / f"{record.id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("idempotent")
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert store.get(record.id).idempotent is None


def test_mutating_transport_failure_is_ambiguous(tmp_path) -> None:
    store = RunStore(tmp_path)
    record = store.create(
        operation="run_script",
        target="example",
        timeout_seconds=30,
        may_mutate=True,
        script_id="system.example",
        argument_names=["zeta", "alpha"],
    )
    finished = store.finish(record.id, _execution("transport_error"))

    assert finished.ambiguous is True
    assert finished.argument_names == ["alpha", "zeta"]


def test_read_only_timeout_is_not_ambiguous(tmp_path) -> None:
    store = RunStore(tmp_path)
    record = store.create(
        operation="run_script",
        target="example",
        timeout_seconds=30,
        may_mutate=False,
        script_id="system.inspect",
    )
    finished = store.finish(record.id, _execution("timeout"))

    assert finished.status == "timeout"
    assert finished.ambiguous is False


def test_running_records_become_interrupted_on_store_start(tmp_path) -> None:
    first = RunStore(tmp_path)
    record = first.create(
        operation="run_shell",
        target="example",
        timeout_seconds=30,
        may_mutate=True,
    )

    second = RunStore(tmp_path)
    recovered = second.get(record.id)

    assert recovered.status == "interrupted"
    assert recovered.ambiguous is True
    assert recovered.error_type == "ServerRestart"


def test_retention_removes_only_old_unambiguous_terminal_runs(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    store = RunStore(tmp_path, completed_days=30, now=clock)

    old = store.create(
        operation="run_command",
        target="example",
        timeout_seconds=30,
        may_mutate=False,
    )
    store.finish(old.id, _execution())

    retained = store.create(
        operation="run_command",
        target="example",
        timeout_seconds=30,
        may_mutate=False,
    )
    store.finish(retained.id, _execution())
    store.set_retained(retained.id, True)

    ambiguous = store.create(
        operation="run_command",
        target="example",
        timeout_seconds=30,
        may_mutate=True,
    )
    store.finish(ambiguous.id, _execution("transport_error"))

    clock.value += timedelta(days=31)
    removed = store.cleanup()

    assert removed == [old.id]
    assert store.get(retained.id).retained is True
    assert store.get(ambiguous.id).ambiguous is True


def test_list_runs_filters_and_orders(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    store = RunStore(tmp_path, now=clock)
    first = store.create(
        operation="run_command",
        target="one",
        timeout_seconds=30,
        may_mutate=False,
        task_id="task-example",
    )
    store.finish(first.id, _execution())
    clock.value += timedelta(seconds=1)
    second = store.create(
        operation="run_command",
        target="two",
        timeout_seconds=30,
        may_mutate=False,
    )
    store.finish(second.id, _execution())

    assert [record.id for record in store.list()] == [second.id, first.id]
    assert [record.id for record in store.list(task_id="task-example")] == [first.id]
    assert [record.id for record in store.list(status="succeeded", limit=1)] == [second.id]

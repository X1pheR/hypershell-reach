"""Bounded, one-way, SSH-delivered Reach peer metadata convergence.

Imported records are read-only, origin-qualified mirrors. No imported record
enters the executable local Task/Run stores, and no mutation is replayed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import ReachConfig
from .database import ReachDatabase
from .execution import build_ssh_argv
from .runs import RunRecord
from .tasks import PENDING_MUTATION_BLOCKER_PREFIX, TaskRecord, TaskStore, new_task_id

NODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
MAX_BATCH_BYTES = 512 * 1024
MAX_ITEMS = 50
INTERVAL_SECONDS = 120
PREFIX = "peer/v1/"
_KINDS = {"task": TaskRecord, "run": RunRecord}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("ascii")).hexdigest()


def _key(*parts: str) -> str:
    return PREFIX + "/".join(parts)


def _metadata(conn, key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _store_metadata(conn, key: str, value: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO metadata(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, _canonical(value)),
    )


class PeerItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: str
    record_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, Any]
    fenced: bool = False

    @model_validator(mode="after")
    def validate_record(self) -> PeerItem:
        model = _KINDS.get(self.kind)
        if model is None:
            raise ValueError("unknown peer record kind")
        record = model.model_validate(self.payload)
        if record.id != self.record_id:
            raise ValueError("record ID does not match payload")
        if self.fenced and self.kind != "task":
            raise ValueError("only tasks can be fenced")
        if _digest({"record": self.payload, "fenced": self.fenced}) != self.digest:
            raise ValueError("peer payload checksum mismatch")
        return self


class PeerBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol: int = 1
    origin: str
    destination: str
    items: list[PeerItem] = Field(max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def valid_identity(self) -> PeerBatch:
        if self.protocol != 1 or not NODE_RE.fullmatch(self.origin) or not NODE_RE.fullmatch(self.destination):
            raise ValueError("unsupported peer protocol or node identity")
        if self.origin == self.destination:
            raise ValueError("cannot import from local peer")
        if len({(x.kind, x.record_id) for x in self.items}) != len(self.items):
            raise ValueError("duplicate peer record in batch")
        return self


def import_batch(config: ReachConfig, body: bytes, *, authenticated_origin: str) -> dict[str, Any]:
    """Import metadata into the active peer, transactionally and without execution authority."""
    topology = config.topology
    if topology is None or topology.effective_mode != "active" or not config.workspace.database:
        raise ValueError("peer import requires configured active unified persistence")
    if len(body) > MAX_BATCH_BYTES:
        raise ValueError("peer batch exceeds size limit")
    batch = PeerBatch.model_validate_json(body)
    if batch.destination != topology.node_id or batch.origin != authenticated_origin:
        raise ValueError("unexpected peer identity")
    if not NODE_RE.fullmatch(authenticated_origin):
        raise ValueError("invalid authenticated source")
    db = ReachDatabase(config.workspace.database)
    accepted = []
    with db.connection(write=True) as conn:
        for item in batch.items:
            key = _key("received", batch.origin, item.kind, item.record_id)
            current = _metadata(conn, key)
            if current is not None:
                if item.version < current["version"]:
                    raise ValueError("stale peer record version")
                if item.version == current["version"] and item.digest != current["digest"]:
                    raise ValueError("conflicting peer record checksum")
            if current is None or item.version > current["version"]:
                _store_metadata(conn, key, {
                    "origin": batch.origin,
                    "version": item.version,
                    "digest": item.digest,
                    "payload": item.payload,
                })
            entry = {"kind": item.kind, "id": item.record_id,
                     "version": item.version, "digest": item.digest}
            if item.kind == "task" and not item.fenced:
                source = TaskRecord.model_validate(item.payload)
                identity = json.dumps(TaskStore._continuity_identity(
                    source.title, source.objective, source.project_ref))
                collision = conn.execute(
                    "SELECT 1 FROM tasks WHERE identity=? AND archived=0 "
                    "AND status IN ('active','partial','blocked') LIMIT 1",
                    (identity,)
                ).fetchone()
                if collision:
                    entry["handoff_conflict"] = True
            if item.kind == "task" and item.fenced:
                map_key = _key("mapping", batch.origin, item.record_id)
                mapping = _metadata(conn, map_key)
                if mapping is None:
                    source = TaskRecord.model_validate(item.payload)
                    identity = json.dumps(TaskStore._continuity_identity(
                        source.title, source.objective, source.project_ref))
                    if conn.execute(
                        "SELECT 1 FROM tasks WHERE identity=? AND archived=0 "
                        "AND status IN ('active','partial','blocked') LIMIT 1",
                        (identity,)
                    ).fetchone():
                        raise ValueError("equivalent Home Task requires reconciliation")
                    if source.status not in {"active", "partial", "blocked"}:
                        raise ValueError("no ownership mapping for terminal fenced task")
                    if any(x.startswith(PENDING_MUTATION_BLOCKER_PREFIX)
                           for x in source.continuity.blockers):
                        raise ValueError("cannot transfer a task with pending mutation")
                    now = datetime.now(UTC).isoformat()
                    # A new local ID is intentional: source IDs remain read-only
                    # foreign origin IDs, with an immutable mapping for traceability.
                    while True:
                        new_id = new_task_id()
                        if not conn.execute(
                            "SELECT 1 FROM tasks WHERE id=?", (new_id,)
                        ).fetchone():
                            break
                    local = source.model_copy(update={
                        "id": new_id, "schema_version": 2, "revision": 1,
                        "created_at": now, "updated_at": now, "archived_at": None,
                    })
                    TaskStore._put(conn, local, archived=False)
                    mapping = {"home_task_id": new_id, "origin": batch.origin,
                               "origin_task_id": item.record_id, "promoted_at": now}
                    _store_metadata(conn, map_key, mapping)
                entry["home_task_id"] = mapping["home_task_id"]
            accepted.append(entry)
        if accepted:
            _store_metadata(conn, _key("last-import", batch.origin), {
                "received_at": datetime.now(UTC).isoformat(),
                "count": len(accepted),
            })
    return {"protocol": 1, "origin": batch.origin, "destination": batch.destination,
            "accepted": accepted}


def prepare_batch(config: ReachConfig) -> PeerBatch:
    """Snapshot changed source metadata and advance local per-record versions only."""
    topology = config.topology
    if (topology is None or topology.effective_mode != "passive"
            or not topology.primary_target or not config.workspace.database):
        raise ValueError("peer sync requires configured passive primary target and database")
    db = ReachDatabase(config.workspace.database)
    ready: list[PeerItem] = []
    size = 100
    with db.connection(write=True) as conn:
        # Queries and metadata revisions share one SQLite write transaction.
        # Scan Task + Run payload snapshots rather than relying on timestamp:
        # retain toggles and abnormal Run transitions may not change a clock.
        for kind, table in (("task", "tasks"), ("run", "runs")):
            rows = conn.execute(f"SELECT id,payload FROM {table} ORDER BY id").fetchall()
            for row in rows:
                record_id, raw = row
                payload = json.loads(raw)
                if kind == "task":
                    TaskRecord.model_validate(payload)
                else:
                    RunRecord.model_validate(payload)
                fenced = bool(
                    kind == "task" and conn.execute(
                        "SELECT 1 FROM metadata WHERE key=?",
                        (_key("fence", "task", record_id),)
                    ).fetchone()
                )
                digest = _digest({"record": payload, "fenced": fenced})
                key = _key("outbound", kind, record_id)
                old = _metadata(conn, key)
                if old is None or old["digest"] != digest:
                    new = {"version": (old["version"] + 1 if old else 1),
                           "digest": digest, "acked_version": old["acked_version"] if old else 0}
                    _store_metadata(conn, key, new)
                else:
                    new = old
                if new["acked_version"] >= new["version"]:
                    continue
                item = PeerItem(kind=kind, record_id=record_id,
                                version=new["version"], digest=digest,
                                payload=payload, fenced=fenced)
                encoded = len(_canonical(item.model_dump()).encode("ascii"))
                if len(ready) < MAX_ITEMS and size + encoded < MAX_BATCH_BYTES:
                    ready.append(item)
                    size += encoded
    return PeerBatch(origin=topology.node_id, destination=topology.primary_node_id, items=ready)


def fence_eligible(config: ReachConfig) -> list[str]:
    """Fence ACKed, idle, unambiguous passive Tasks without active leases.

    The fence is written inside the same SQLite writer transaction used by
    Task/Run admissions; once set, source execution and mutation fail closed.
    """
    if (config.topology is None or config.topology.effective_mode != "passive"
            or not config.workspace.database):
        raise ValueError("only passive unified peers may fence Tasks")
    db = ReachDatabase(config.workspace.database)
    fenced: list[str] = []
    with db.connection(write=True) as conn:
        tasks = conn.execute(
            "SELECT id,payload FROM tasks WHERE archived=0 AND status IN ('active','partial','blocked')"
            " ORDER BY id LIMIT 100"
        ).fetchall()
        for row in tasks:
            task_id, raw = row
            fence_key = _key("fence", "task", task_id)
            if (_metadata(conn, fence_key) is not None or
                    _metadata(conn, _key("handoff-blocked", "task", task_id)) is not None):
                continue
            record = TaskRecord.model_validate_json(raw)
            if any(b.startswith(PENDING_MUTATION_BLOCKER_PREFIX)
                   for b in record.continuity.blockers):
                continue
            # A potentially recoverable expired lease is still ownership
            # evidence. Do not infer it is safe to steal.
            if conn.execute(
                "SELECT 1 FROM task_leases WHERE task_id=?", (task_id,)
            ).fetchone():
                continue
            related = conn.execute(
                "SELECT id,status,ambiguous,payload FROM runs WHERE task_id=?",
                (task_id,)).fetchall()
            if any(r["ambiguous"] or r["status"] in {"running", "unknown", "interrupted"}
                   for r in related):
                continue
            linked_runs_synced = True
            for linked in related:
                outcome = _metadata(conn, _key("outbound", "run", linked["id"]))
                if outcome is None or outcome["acked_version"] != outcome["version"]:
                    linked_runs_synced = False
                    break
            if not linked_runs_synced:
                continue
            out = _metadata(conn, _key("outbound", "task", task_id))
            if (out is None or out["acked_version"] != out["version"]
                    or out["digest"] != _digest({"record": json.loads(raw), "fenced": False})):
                continue
            _store_metadata(conn, fence_key, {
                "fenced_at": datetime.now(UTC).isoformat(),
                "task_revision": record.revision,
                "source_digest": out["digest"],
            })
            fenced.append(task_id)
    return fenced


def acknowledge(config: ReachConfig, batch: PeerBatch, receipt: dict[str, Any]) -> None:
    """Only persist ACK markers that exactly match the submitted batch."""
    if receipt.get("origin") != batch.origin or receipt.get("destination") != batch.destination or receipt.get("protocol") != 1:
        raise ValueError("peer receipt identity mismatch")
    accepted = receipt.get("accepted")
    if not isinstance(accepted, list) or len(accepted) != len(batch.items):
        raise ValueError("peer receipt does not match outbound records")
    for item, response in zip(batch.items, accepted):
        expected = {"kind": item.kind, "id": item.record_id,
                    "version": item.version, "digest": item.digest}
        if item.kind == "task" and not item.fenced and isinstance(response, dict):
            if response.get("handoff_conflict") is True:
                expected["handoff_conflict"] = True
        if item.kind == "task" and item.fenced:
            home_id = response.get("home_task_id") if isinstance(response, dict) else None
            if not isinstance(home_id, str) or not home_id.startswith("task-"):
                raise ValueError("fenced task missing primary ownership mapping")
            expected["home_task_id"] = home_id
        if response != expected:
            raise ValueError("peer receipt does not match outbound records")
    db = ReachDatabase(config.workspace.database)
    with db.connection(write=True) as conn:
        for item in batch.items:
            key = _key("outbound", item.kind, item.record_id)
            state = _metadata(conn, key)
            if state is None or state["version"] < item.version:
                raise ValueError("outbound record no longer exists")
            if state["version"] == item.version and state["digest"] != item.digest:
                raise ValueError("outbound record changed without new revision")
            if state["version"] == item.version:
                state["acked_version"] = item.version
                _store_metadata(conn, key, state)
            if (item.kind == "task" and not item.fenced
                    and accepted[batch.items.index(item)].get("handoff_conflict")):
                _store_metadata(conn, _key("handoff-blocked", "task", item.record_id), {
                    "reason": "equivalent Home Task requires operator reconciliation",
                    "observed_at": datetime.now(UTC).isoformat(),
                })
            if item.kind == "task" and item.fenced:
                source_fence = _metadata(conn, _key("fence", "task", item.record_id))
                if source_fence is None:
                    raise ValueError("source task is not fenced for transfer")
                home_id = accepted[batch.items.index(item)]["home_task_id"]
                existing = _metadata(conn, _key("mapping", batch.destination, item.record_id))
                if existing is not None and existing["home_task_id"] != home_id:
                    raise ValueError("source task has conflicting Home mapping")
                if existing is None:
                    _store_metadata(conn, _key("mapping", batch.destination, item.record_id),
                                    {"home_task_id": home_id, "origin_task_id": item.record_id})
                row = conn.execute(
                    "SELECT payload,archived FROM tasks WHERE id=?", (item.record_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("source task disappeared during handoff")
                record = TaskRecord.model_validate_json(row["payload"])
                if record.status == "transferred":
                    continue
                if record.status not in {"active", "partial", "blocked"} or row["archived"]:
                    raise ValueError("source Task changed after fencing")
                if record.model_dump() != item.payload:
                    raise ValueError("source Task changed during handoff")
                if any(x.startswith(PENDING_MUTATION_BLOCKER_PREFIX)
                       for x in record.continuity.blockers):
                    raise ValueError("source has unresolved mutation")
                now = datetime.now(UTC).isoformat()
                archived = record.model_copy(update={
                    "schema_version": 3, "status": "transferred", "next_action": None,
                    "archived_at": now, "updated_at": now,
                    "revision": record.revision + 1,
                })
                TaskStore._put(conn, archived, archived=True)
        _store_metadata(conn, _key("last-sync", batch.destination), {
            "succeeded_at": datetime.now(UTC).isoformat(),
            "count": len(batch.items),
        })


def prune_acknowledged(config: ReachConfig) -> dict[str, int]:
    """Delete only source-local, ACKed, retention-eligible records.

    Retained, ambiguous, running or unknown outcomes never qualify.
    Home's imported mirror is not removed by source cleanup.
    """
    if (config.topology is None or config.topology.effective_mode != "passive"
            or not config.workspace.database):
        raise ValueError("acknowledged pruning requires passive unified persistence")
    removed = {"runs": 0, "tasks": 0}
    db = ReachDatabase(config.workspace.database)
    now = datetime.now(UTC)
    with db.connection(write=True) as conn:
        for table, kind, retention, time_field, condition in (
            ("runs", "run", config.retention.runs.completed_days, "ended_at",
             "retained=0 AND ambiguous=0 AND status IN ('succeeded','remote_error','transport_error','timeout','local_error')"),
            ("tasks", "task", config.retention.tasks.archived_days, "archived_at",
             "archived=1 AND retained=0"),
        ):
            if retention is None:
                continue
            cutoff = (now - timedelta(days=retention)).timestamp()
            rows = conn.execute(
                f"SELECT id,payload FROM {table} WHERE {condition} AND {time_field}<=? ORDER BY id LIMIT 500",
                (cutoff,)).fetchall()
            for row in rows:
                record_id, raw = row
                state = _metadata(conn, _key("outbound", kind, record_id))
                if state is None or state["acked_version"] != state["version"]:
                    continue
                # Even if the payload changed after outbox snapshot, cleanup
                # cannot delete a newer unsent state.
                payload = json.loads(raw)
                if state["digest"] != _digest({"record": payload, "fenced": bool(
                    kind == "task" and conn.execute(
                        "SELECT 1 FROM metadata WHERE key=?",
                        (_key("fence", "task", record_id),)
                    ).fetchone()
                )}):
                    continue
                conn.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
                removed[table] += 1
    return removed


async def sync_once(config: ReachConfig) -> dict[str, Any]:
    """One safe private SSH transfer, bounded; neither timeout nor retry repeats work."""
    batch = prepare_batch(config)
    if not batch.items:
        return {"status": "idle", "count": 0}
    assert config.topology is not None and config.topology.primary_target is not None
    target = config.enabled_target(config.topology.primary_target)
    # Fixed command: the source peer cannot choose shell/CLI flags from state.
    # The deployment owns how the primary host invokes the Reach container.
    # The fixed SSH remote command is a narrow deployment-installed importer,
    # not a host-specific Docker command embedded in the generic product.
    remote = "reach-peer-import --origin " + shlex.quote(batch.origin)
    argv = build_ssh_argv(target, config.defaults.connect_timeout_seconds, remote)
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate((_canonical(batch.model_dump()) + "\n").encode("ascii")), timeout=45)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("peer sync transport timeout; import outcome unknown")
    if process.returncode != 0 or len(stdout) > MAX_BATCH_BYTES or len(stderr) > 8192:
        raise RuntimeError("peer sync transport/import failed; no ACK committed")
    try:
        receipt = json.loads(stdout)
        acknowledge(config, batch, receipt)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("invalid or mismatched peer receipt; no ACK committed") from exc
    return {"status": "synced", "count": len(batch.items)}


def _sync_failure(config: ReachConfig, category: str) -> None:
    if config.topology is None or not config.workspace.database:
        return
    with ReachDatabase(config.workspace.database).connection(write=True) as conn:
        _store_metadata(conn, _key("last-failure", config.topology.primary_node_id), {
            "failed_at": datetime.now(UTC).isoformat(),
            # The error type, never stderr/host/key/command data.
            "category": category,
        })


async def passive_sync_loop(config: ReachConfig) -> None:
    """Bounded retry cadence. A primary outage never damages passive state."""
    while True:
        try:
            await sync_once(config)
            fence_eligible(config)
            prune_acknowledged(config)
        except Exception as exc:
            try:
                _sync_failure(config, type(exc).__name__)
            except (OSError, RuntimeError):
                # A corrupted/unavailable local DB must not crash the Reach server;
                # peer_status will still surface failure to query persistent state.
                pass
        await asyncio.sleep(INTERVAL_SECONDS)


def peer_status(config: ReachConfig) -> dict[str, Any]:
    topology = config.topology
    if topology is None or not config.workspace.database:
        return {"enabled": False}
    result: dict[str, Any] = {"mode": topology.effective_mode,
                              "node_id": topology.node_id,
                              "primary_node_id": topology.primary_node_id,
                              "enabled": topology.effective_mode == "active" or bool(topology.primary_target)}
    with ReachDatabase(config.workspace.database, read_only=True).connection() as conn:
        if topology.effective_mode == "active":
            rows = conn.execute(
                "SELECT key,value FROM metadata WHERE key LIKE ? ORDER BY key",
                (_key("last-import", "%"),)).fetchall()
            result["sources"] = {r[0].split("/")[-1]: json.loads(r[1]) for r in rows}
            result["imported_tasks"] = conn.execute(
                "SELECT COUNT(*) FROM metadata WHERE key LIKE ?", (_key("received", "%", "task", "%"),)
            ).fetchone()[0]
            result["imported_runs"] = conn.execute(
                "SELECT COUNT(*) FROM metadata WHERE key LIKE ?", (_key("received", "%", "run", "%"),)
            ).fetchone()[0]
        else:
            rows = conn.execute("SELECT value FROM metadata WHERE key LIKE ?",
                                (_key("outbound", "%"),)).fetchall()
            result["handoff_blocked"] = conn.execute(
                "SELECT COUNT(*) FROM metadata WHERE key LIKE ?",
                (_key("handoff-blocked", "task", "%"),)
            ).fetchone()[0]
            result["pending_records"] = sum(
                json.loads(row[0])["acked_version"] < json.loads(row[0])["version"] for row in rows)
            result["last_sync"] = _metadata(conn, _key("last-sync", topology.primary_node_id))
            result["last_failure"] = _metadata(conn, _key("last-failure", topology.primary_node_id))
    return result


def imported_items(config: ReachConfig, *, kind: str, limit: int = 100) -> list[dict[str, Any]]:
    if kind not in _KINDS or not config.workspace.database:
        raise ValueError("invalid imported record query")
    if limit < 1 or limit > 500:
        raise ValueError("invalid imported record limit")
    prefix = _key("received", "%", kind, "%")
    with ReachDatabase(config.workspace.database, read_only=True).connection() as conn:
        rows = conn.execute(
            "SELECT value FROM metadata WHERE key LIKE ? ORDER BY key DESC LIMIT ?",
            (prefix, limit)).fetchall()
    result = []
    with ReachDatabase(config.workspace.database, read_only=True).connection() as conn:
        for row in rows:
            state = json.loads(row[0])
            record = _KINDS[kind].model_validate(state["payload"])
            value = {"origin": state["origin"], "version": state["version"],
                     "record": record.summary()}
            if kind == "task":
                mapping = _metadata(conn, _key("mapping", state["origin"], record.id))
                if mapping is not None:
                    value["home_task_id"] = mapping["home_task_id"]
            result.append(value)
    return result

from __future__ import annotations

import yaml
import pytest
from pydantic import ValidationError

from hypershell_reach.candidates import CandidateRecord


def candidate_payload(candidate_id: str = "ATR-022") -> dict:
    return {
        "schema_version": 1,
        "id": candidate_id,
        "revision": 1,
        "title": "OIDC integration migration preflight",
        "problem": {
            "summary": "Repeated OIDC migrations require the same preflight checks.",
            "cause": "The checks are currently reconstructed ad hoc for each migration.",
            "recurrence": "The same sequence has been needed across multiple OIDC integrations.",
            "evidence": ["Repeated operator workflow with deterministic preconditions."],
        },
        "proposal": {
            "capability": "Validate OIDC migration preconditions without changing provider state.",
            "proposed_tool_id": "oidc.migration-preflight",
            "required_inputs": [
                {"name": "provider_id", "description": "Stable provider identifier."}
            ],
            "expected_outputs": [
                {"name": "preflight", "description": "Deterministic validation result."}
            ],
            "safety": {
                "mutating": False,
                "secret_access": False,
                "boundary": "Read-only provider metadata and configuration checks only.",
            },
            "acceptance": [
                "Returns a deterministic pass/fail result for every declared precondition."
            ],
        },
        "ownership": {"owner_id": "X1pheR/hypershell-reach"},
        "promotion": {
            "state": "candidate",
            "rationale": "The recurring bounded workflow is reusable and deterministic.",
            "state_reason": None,
        },
        "implementation": {"task_id": None, "final_reference": None},
        "created_at": "2026-08-22T15:00:00.000000Z",
        "updated_at": "2026-08-22T15:00:00.000000Z",
    }


def test_valid_candidate_record_roundtrip() -> None:
    record = CandidateRecord.model_validate(candidate_payload())
    encoded = yaml.safe_dump(record.model_dump(mode="json"), sort_keys=False)

    decoded = CandidateRecord.model_validate(yaml.safe_load(encoded))

    assert decoded == record
    assert decoded.proposal.proposed_tool_id == "oidc.migration-preflight"


def test_candidate_record_rejects_unknown_fields() -> None:
    payload = candidate_payload()
    payload["unknown"] = "must fail"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CandidateRecord.model_validate(payload)


def test_candidate_requires_problem_proposal_safety_ownership_and_acceptance_content() -> None:
    payload = candidate_payload()
    payload["problem"]["cause"] = ""
    payload["proposal"]["acceptance"] = []
    payload["proposal"]["safety"]["boundary"] = ""
    payload["ownership"]["owner_id"] = ""

    with pytest.raises(ValidationError) as exc_info:
        CandidateRecord.model_validate(payload)

    locations = {tuple(error["loc"]) for error in exc_info.value.errors()}
    assert ("problem", "cause") in locations
    assert ("proposal", "acceptance") in locations
    assert ("proposal", "safety", "boundary") in locations
    assert ("ownership", "owner_id") in locations

from concurrent.futures import ThreadPoolExecutor
import os
import stat

from hypershell_reach.candidates import (
    CandidateOwnership,
    CandidateProblem,
    CandidateProposal,
    CandidateStore,
)


def create_candidate(store: CandidateStore, candidate_id: str = "ATR-022") -> CandidateRecord:
    payload = candidate_payload(candidate_id)
    return store.create(
        candidate_id=candidate_id,
        title=payload["title"],
        problem=CandidateProblem.model_validate(payload["problem"]),
        proposal=CandidateProposal.model_validate(payload["proposal"]),
        ownership=CandidateOwnership.model_validate(payload["ownership"]),
        promotion_rationale=payload["promotion"]["rationale"],
    )


def test_candidate_store_create_get_and_yaml_roundtrip(tmp_path) -> None:
    store = CandidateStore(tmp_path / "candidates")

    created = create_candidate(store)
    loaded = store.get(created.id)

    assert loaded == created
    assert (tmp_path / "candidates" / "ATR-022.yaml").is_file()
    assert loaded.revision == 1


def test_candidate_store_rejects_stale_expected_revision(tmp_path) -> None:
    store = CandidateStore(tmp_path / "candidates")
    created = create_candidate(store)
    updated = store.update(created.id, expected_revision=1, title="Updated title")

    with pytest.raises(ValueError, match="stale candidate revision"):
        store.update(created.id, expected_revision=1, title="Lost update")

    assert updated.revision == 2
    assert store.get(created.id).title == "Updated title"


def test_candidate_store_rejects_duplicate_candidate_ids(tmp_path) -> None:
    root = tmp_path / "candidates"
    store = CandidateStore(root)
    record = create_candidate(store)
    duplicate = record.model_copy(update={"title": "Duplicate"})
    (root / "duplicate.yaml").write_text(
        yaml.safe_dump(duplicate.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="duplicate candidate ID"):
        store.list()


def test_candidate_store_fsyncs_file_and_parent_directory(tmp_path, monkeypatch) -> None:
    import hypershell_reach.candidates as candidates_module

    fsynced_modes: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        fsynced_modes.append(os.fstat(fd).st_mode)
        real_fsync(fd)

    monkeypatch.setattr(candidates_module.os, "fsync", recording_fsync)
    store = CandidateStore(tmp_path / "candidates")

    create_candidate(store)

    assert any(stat.S_ISREG(mode) for mode in fsynced_modes)
    assert any(stat.S_ISDIR(mode) for mode in fsynced_modes)


def test_concurrent_candidate_updates_cannot_silently_lose_state(tmp_path) -> None:
    store_a = CandidateStore(tmp_path / "candidates")
    store_b = CandidateStore(tmp_path / "candidates")
    create_candidate(store_a)

    def update(store: CandidateStore, title: str):
        try:
            return ("ok", store.update("ATR-022", expected_revision=1, title=title).title)
        except ValueError as exc:
            return ("error", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: update(*args), [(store_a, "First"), (store_b, "Second")]))

    assert sorted(result[0] for result in results) == ["error", "ok"]
    committed = store_a.get("ATR-022")
    assert committed.revision == 2
    assert committed.title in {"First", "Second"}

from hypershell_reach.candidates import CandidateReference


def test_explicit_approval_transition_works(tmp_path) -> None:
    store = CandidateStore(tmp_path / "candidates")
    create_candidate(store)

    approved = store.transition(
        "ATR-022",
        expected_revision=1,
        target_state="approved",
        state_reason="Operator explicitly approved implementation.",
    )

    assert approved.promotion.state == "approved"
    assert approved.promotion.state_reason == "Operator explicitly approved implementation."
    assert approved.revision == 2


def test_blocked_and_not_warranted_transitions_preserve_rationale(tmp_path) -> None:
    blocked_store = CandidateStore(tmp_path / "blocked")
    create_candidate(blocked_store)
    blocked = blocked_store.transition(
        "ATR-022", expected_revision=1, target_state="blocked", state_reason="Upstream API is unstable."
    )
    assert blocked.promotion.rationale.startswith("The recurring bounded workflow")
    assert blocked.promotion.state_reason == "Upstream API is unstable."

    rejected_store = CandidateStore(tmp_path / "rejected")
    create_candidate(rejected_store)
    rejected = rejected_store.transition(
        "ATR-022",
        expected_revision=1,
        target_state="not-warranted",
        state_reason="A maintained existing capability now covers the workflow.",
    )
    assert rejected.promotion.rationale.startswith("The recurring bounded workflow")
    assert rejected.promotion.state_reason.startswith("A maintained existing capability")


def test_implemented_or_automated_requires_final_capability_reference(tmp_path) -> None:
    store = CandidateStore(tmp_path / "candidates")
    create_candidate(store)
    store.transition(
        "ATR-022", expected_revision=1, target_state="approved", state_reason="Operator approved."
    )

    with pytest.raises(ValueError, match="final capability reference"):
        store.transition(
            "ATR-022", expected_revision=2, target_state="implemented", state_reason="Code completed."
        )

    final = store.transition(
        "ATR-022",
        expected_revision=2,
        target_state="automated",
        state_reason="Managed tool is implemented and accepted.",
        final_reference=CandidateReference(kind="managed-tool", id="oidc.migration-preflight"),
    )
    assert final.implementation.final_reference is not None
    assert final.implementation.final_reference.id == "oidc.migration-preflight"


def test_candidate_task_reference_validates_safely(tmp_path) -> None:
    store = CandidateStore(tmp_path / "candidates")
    create_candidate(store)
    store.transition(
        "ATR-022", expected_revision=1, target_state="approved", state_reason="Operator approved."
    )

    with pytest.raises(ValueError, match="invalid implementation task ID"):
        store.link_task("ATR-022", expected_revision=2, task_id="../../task")

    linked = store.link_task(
        "ATR-022",
        expected_revision=2,
        task_id="task-20260822T153707298168Z-a8eaece9f727",
    )
    assert linked.implementation.task_id == "task-20260822T153707298168Z-a8eaece9f727"
    assert linked.revision == 3


def test_candidate_atomic_write_failure_preserves_committed_record(tmp_path, monkeypatch) -> None:
    import hypershell_reach.candidates as candidates_module

    store = CandidateStore(tmp_path / "candidates")
    original = create_candidate(store)

    def fail_replace(source, destination):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(candidates_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated rename failure"):
        store.update("ATR-022", expected_revision=1, title="Must not commit")

    assert store.get("ATR-022") == original
    assert not list((tmp_path / "candidates").glob(".ATR-022.*.tmp"))


def _process_candidate_update(root: str, title: str, gate, queue) -> None:
    store = CandidateStore(root)
    gate.wait(timeout=5)
    try:
        updated = store.update("ATR-022", expected_revision=1, title=title)
        queue.put(("ok", updated.revision, updated.title))
    except ValueError as exc:
        queue.put(("error", str(exc)))


def test_interprocess_candidate_updates_are_serialized_by_candidate_lock(tmp_path) -> None:
    import multiprocessing

    root = tmp_path / "candidates"
    create_candidate(CandidateStore(root))
    context = multiprocessing.get_context("fork")
    gate = context.Event()
    queue = context.Queue()
    processes = [
        context.Process(target=_process_candidate_update, args=(str(root), title, gate, queue))
        for title in ("Process one", "Process two")
    ]
    for process in processes:
        process.start()
    gate.set()
    results = [queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert sorted(result[0] for result in results) == ["error", "ok"]
    committed = CandidateStore(root).get("ATR-022")
    assert committed.revision == 2
    assert committed.title in {"Process one", "Process two"}

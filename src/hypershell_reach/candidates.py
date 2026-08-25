from __future__ import annotations

import fcntl
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Iterator, Literal
from uuid import uuid4

import yaml

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CandidateState = Literal[
    "candidate",
    "approved",
    "blocked",
    "not-warranted",
    "implemented",
    "automated",
]
CandidateReferenceKind = Literal["managed-tool", "capability"]
BoundedCandidateText = Annotated[str, Field(min_length=1, max_length=2_000)]

_CANDIDATE_ID = re.compile(r"^[A-Z][A-Z0-9-]{1,63}$")
_STABLE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,189}$")
_TOOL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}\.[a-z0-9][a-z0-9._-]{0,126}$")
_TASK_ID = re.compile(r"^task-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")


class CandidateProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: BoundedCandidateText
    cause: BoundedCandidateText
    recurrence: BoundedCandidateText
    evidence: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = Field(
        min_length=1, max_length=20
    )


class CandidateInterfaceField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)


class CandidateSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mutating: bool
    secret_access: bool
    boundary: BoundedCandidateText


class CandidateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: BoundedCandidateText
    proposed_tool_id: str | None = Field(default=None, min_length=3, max_length=190)
    required_inputs: list[CandidateInterfaceField] = Field(max_length=50)
    expected_outputs: list[CandidateInterfaceField] = Field(min_length=1, max_length=50)
    safety: CandidateSafety
    acceptance: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = Field(
        min_length=1, max_length=50
    )

    @field_validator("proposed_tool_id")
    @classmethod
    def validate_proposed_tool_id(cls, value: str | None) -> str | None:
        if value is not None and not _TOOL_ID.fullmatch(value):
            raise ValueError("invalid proposed tool ID")
        return value


class CandidateOwnership(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: str = Field(min_length=1, max_length=190)

    @field_validator("owner_id")
    @classmethod
    def validate_owner_id(cls, value: str) -> str:
        if not _STABLE_REFERENCE.fullmatch(value):
            raise ValueError("invalid owner ID")
        return value


class CandidatePromotion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: CandidateState = "candidate"
    rationale: BoundedCandidateText
    state_reason: str | None = Field(default=None, min_length=1, max_length=2_000)


class CandidateReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CandidateReferenceKind
    id: str = Field(min_length=1, max_length=190)

    @field_validator("id")
    @classmethod
    def validate_reference_id(cls, value: str) -> str:
        if not _STABLE_REFERENCE.fullmatch(value):
            raise ValueError("invalid final capability reference")
        return value

    @model_validator(mode="after")
    def validate_managed_tool_reference(self) -> "CandidateReference":
        if self.kind == "managed-tool" and not _TOOL_ID.fullmatch(self.id):
            raise ValueError("invalid managed tool reference")
        return self


class CandidateImplementation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    final_reference: CandidateReference | None = None

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str | None) -> str | None:
        if value is not None and not _TASK_ID.fullmatch(value):
            raise ValueError("invalid implementation task ID")
        return value


class CandidateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str
    revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    problem: CandidateProblem
    proposal: CandidateProposal
    ownership: CandidateOwnership
    promotion: CandidatePromotion
    implementation: CandidateImplementation = Field(default_factory=CandidateImplementation)
    created_at: str
    updated_at: str

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _CANDIDATE_ID.fullmatch(value):
            raise ValueError("invalid candidate ID")
        return value

    @model_validator(mode="after")
    def validate_lifecycle_shape(self) -> "CandidateRecord":
        if self.promotion.state == "candidate" and self.promotion.state_reason is not None:
            raise ValueError("candidate state must not have a state reason")
        if self.promotion.state != "candidate" and self.promotion.state_reason is None:
            raise ValueError(f"{self.promotion.state} state requires a state reason")
        if self.promotion.state in {"implemented", "automated"}:
            if self.implementation.final_reference is None:
                raise ValueError(f"{self.promotion.state} state requires a final capability reference")
        elif self.implementation.final_reference is not None:
            raise ValueError("final capability reference is allowed only for implemented or automated state")
        return self



def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


_ALLOWED_TRANSITIONS: dict[CandidateState, set[CandidateState]] = {
    "candidate": {"approved", "blocked", "not-warranted"},
    "approved": {"blocked", "not-warranted", "implemented", "automated"},
    "blocked": {"approved", "not-warranted"},
    "not-warranted": set(),
    "implemented": set(),
    "automated": set(),
}


class CandidateStore:
    def __init__(self, root: str | Path, *, read_only: bool = False) -> None:
        self.root = Path(root)
        self.lock_root = self.root / ".locks"
        self.read_only = read_only
        if not read_only:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
            self.lock_root.mkdir(exist_ok=True, mode=0o750)

    def _require_writable(self) -> None:
        if self.read_only:
            raise RuntimeError("candidate store is read-only")

    def _validate_id(self, candidate_id: str) -> None:
        if not _CANDIDATE_ID.fullmatch(candidate_id):
            raise ValueError("invalid candidate ID")

    def _path(self, candidate_id: str) -> Path:
        self._validate_id(candidate_id)
        return self.root / f"{candidate_id}.yaml"

    @contextmanager
    def _lock(self, candidate_id: str) -> Iterator[None]:
        self._require_writable()
        self._validate_id(candidate_id)
        path = self.lock_root / f"{candidate_id}.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read_path(self, path: Path) -> CandidateRecord:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"invalid candidate record: {path.name}")
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            record = CandidateRecord.model_validate(payload)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise RuntimeError(f"invalid candidate record: {path.name}") from exc
        return record

    def _assert_no_duplicate_id(self, candidate_id: str, *, except_path: Path | None = None) -> None:
        for path in self.root.glob("*.yaml"):
            if except_path is not None and path == except_path:
                continue
            record = self._read_path(path)
            if record.id == candidate_id:
                raise RuntimeError(f"duplicate candidate ID: {candidate_id}")

    def _fsync_directory(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(self.root, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _atomic_write(self, record: CandidateRecord) -> None:
        self._require_writable()
        path = self._path(record.id)
        temporary = self.root / f".{record.id}.{uuid4().hex}.tmp"
        payload = yaml.safe_dump(
            record.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_directory()
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def create(
        self,
        *,
        candidate_id: str,
        title: str,
        problem: CandidateProblem,
        proposal: CandidateProposal,
        ownership: CandidateOwnership,
        promotion_rationale: str,
    ) -> CandidateRecord:
        self._require_writable()
        with self._lock(candidate_id):
            path = self._path(candidate_id)
            if path.exists():
                raise ValueError(f"candidate already exists: {candidate_id}")
            self._assert_no_duplicate_id(candidate_id)
            now = _utc_timestamp()
            record = CandidateRecord(
                id=candidate_id,
                revision=1,
                title=title,
                problem=problem,
                proposal=proposal,
                ownership=ownership,
                promotion=CandidatePromotion(rationale=promotion_rationale),
                created_at=now,
                updated_at=now,
            )
            self._atomic_write(record)
            return record

    def get(self, candidate_id: str) -> CandidateRecord:
        path = self._path(candidate_id)
        if not path.exists():
            raise ValueError(f"unknown candidate: {candidate_id}")
        record = self._read_path(path)
        if record.id != candidate_id:
            raise RuntimeError(f"candidate ID does not match filename: {path.name}")
        return record

    def list(self, *, state: CandidateState | None = None, limit: int = 100) -> list[CandidateRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("candidate list limit must be between 1 and 500")
        records: list[CandidateRecord] = []
        seen: set[str] = set()
        for path in sorted(self.root.glob("*.yaml")):
            record = self._read_path(path)
            if record.id in seen:
                raise RuntimeError(f"duplicate candidate ID: {record.id}")
            seen.add(record.id)
            if path.name != f"{record.id}.yaml":
                raise RuntimeError(f"candidate ID does not match filename: {path.name}")
            records.append(record)
        if state is not None:
            records = [record for record in records if record.promotion.state == state]
        records.sort(key=lambda record: (record.updated_at, record.id), reverse=True)
        return records[:limit]

    def update(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        title: str | None = None,
        problem: CandidateProblem | None = None,
        proposal: CandidateProposal | None = None,
        ownership: CandidateOwnership | None = None,
        promotion_rationale: str | None = None,
    ) -> CandidateRecord:
        self._require_writable()
        with self._lock(candidate_id):
            current = self.get(candidate_id)
            if current.revision != expected_revision:
                raise ValueError(
                    f"stale candidate revision: expected {expected_revision}, current {current.revision}"
                )
            changes: dict[str, object] = {
                "revision": current.revision + 1,
                "updated_at": _utc_timestamp(),
            }
            if title is not None:
                changes["title"] = title
            if problem is not None:
                changes["problem"] = problem
            if proposal is not None:
                changes["proposal"] = proposal
            if ownership is not None:
                changes["ownership"] = ownership
            if promotion_rationale is not None:
                changes["promotion"] = current.promotion.model_copy(
                    update={"rationale": promotion_rationale}
                )
            updated = CandidateRecord.model_validate(current.model_copy(update=changes).model_dump())
            self._atomic_write(updated)
            return updated

    def transition(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        target_state: CandidateState,
        state_reason: str,
        final_reference: CandidateReference | None = None,
    ) -> CandidateRecord:
        self._require_writable()
        with self._lock(candidate_id):
            current = self.get(candidate_id)
            if current.revision != expected_revision:
                raise ValueError(
                    f"stale candidate revision: expected {expected_revision}, current {current.revision}"
                )
            if target_state not in _ALLOWED_TRANSITIONS[current.promotion.state]:
                raise ValueError(
                    f"invalid candidate transition: {current.promotion.state} -> {target_state}"
                )
            if target_state in {"implemented", "automated"} and final_reference is None:
                raise ValueError(f"{target_state} transition requires a final capability reference")
            if target_state not in {"implemented", "automated"} and final_reference is not None:
                raise ValueError(
                    "final capability reference is valid only for implemented or automated transition"
                )
            promotion = current.promotion.model_copy(
                update={"state": target_state, "state_reason": state_reason}
            )
            implementation = current.implementation.model_copy(
                update={"final_reference": final_reference}
            )
            updated = CandidateRecord.model_validate(
                current.model_copy(
                    update={
                        "revision": current.revision + 1,
                        "promotion": promotion,
                        "implementation": implementation,
                        "updated_at": _utc_timestamp(),
                    }
                ).model_dump()
            )
            self._atomic_write(updated)
            return updated

    def link_task(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        task_id: str,
    ) -> CandidateRecord:
        self._require_writable()
        with self._lock(candidate_id):
            current = self.get(candidate_id)
            if current.revision != expected_revision:
                raise ValueError(
                    f"stale candidate revision: expected {expected_revision}, current {current.revision}"
                )
            if current.promotion.state not in {"approved", "blocked"}:
                raise ValueError("implementation task can be linked only after candidate approval")
            implementation = CandidateImplementation(
                task_id=task_id,
                final_reference=current.implementation.final_reference,
            )
            if current.implementation.task_id == task_id:
                return current
            updated = CandidateRecord.model_validate(
                current.model_copy(
                    update={
                        "revision": current.revision + 1,
                        "implementation": implementation,
                        "updated_at": _utc_timestamp(),
                    }
                ).model_dump()
            )
            self._atomic_write(updated)
            return updated


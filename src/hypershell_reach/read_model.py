from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import Any, TypeVar, cast

from .candidates import CandidateStore
from .config import ReachConfig
from .managed_tools import load_tool_registry
from .runs import RunStore
from .skills import inspect_skill_source, inspect_skill_source_summary
from .tasks import TaskStore
from .tooling_registry import ToolingRegistry


T = TypeVar("T")


class ReachReadModel:
    def __init__(self, config: ReachConfig) -> None:
        self.config = config
        self.runs = RunStore(config.workspace.runs, database=config.workspace.database, read_only=True)
        self.tasks = TaskStore(config.workspace.tasks, config.workspace.trash, database=config.workspace.database, read_only=True)
        self.candidate_store = (
            CandidateStore(config.workspace.candidates, database=config.workspace.database, read_only=True)
            if config.workspace.candidates is not None
            else None
        )
        self._cache: dict[str, tuple[float, object]] = {}
        self._cache_lock = Lock()

    def _cached(self, key: str, ttl_seconds: float, loader: Callable[[], T]) -> T:
        now = monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None and cached[0] > now:
                return cast(T, cached[1])
        value = loader()
        with self._cache_lock:
            self._cache[key] = (monotonic() + ttl_seconds, value)
        return value

    def targets(self) -> list[dict[str, Any]]:
        return [
            {
                "id": target_id,
                "display_name": target.display_name,
                "transport": target.transport,
                "capabilities": target.capabilities,
                "enabled": target.enabled,
                "max_timeout_seconds": self.config.resolved_max_timeout(target),
                "max_output_bytes": self.config.resolved_max_output(target),
            }
            for target_id, target in sorted(self.config.targets.items())
        ]

    def tooling(self) -> list[dict[str, Any]]:
        return self._cached(
            "tooling",
            30.0,
            lambda: [script.summary() for script in load_tool_registry(self.config.sources.tools).list()],
        )

    def tool(self, tool_id: str) -> dict[str, Any]:
        return load_tool_registry(self.config.sources.tools).get(tool_id).detail()

    def run_summaries(self, *, limit: int = 100, **filters: Any) -> list[dict[str, Any]]:
        return [record.summary() for record in self.runs.list(limit=limit, **filters)]

    def recent_run_summaries(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return [record.summary() for record in self.runs.recent(limit=limit)]

    def run_count(self, **filters: Any) -> int:
        return self.runs.count(**filters)

    def run(self, run_id: str) -> dict[str, Any]:
        return self.runs.get(run_id).model_dump()

    def task_summaries(self, *, limit: int = 100, **filters: Any) -> list[dict[str, Any]]:
        return self.tasks.query_summaries(limit=limit, **filters)

    def task_count(self, **filters: Any) -> int:
        return self.tasks.count(**filters)

    def operational_counts(self) -> dict[str, int]:
        return {
            "tasks": self.task_count(),
            "archived_tasks": self.task_count(archived=True),
            "active_tasks": sum(self.task_count(status=status) for status in ("active", "partial", "blocked")),
            "blocked_tasks": self.task_count(blocked=True) + self.task_count(status="blocked")
                - self.task_count(status="blocked", blocked=True),
            "runs": self.run_count(),
            "running_runs": self.run_count(status="running"),
            "ambiguous_runs": self.run_count(ambiguous=True),
            "error_runs": sum(self.run_count(status=status) for status in (
                "remote_error", "transport_error", "timeout", "local_error", "interrupted", "unknown")),
        }

    def task(self, task_id: str) -> dict[str, Any]:
        return self.tasks.get(task_id).model_dump()

    def related_run_summaries(self, task_id: str, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        return [record.summary() for record in self.runs.list(task_id=task_id, limit=limit, offset=offset)]

    def skill_source_summaries(self) -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]]:
            reports: list[dict[str, Any]] = []
            for source in self.config.sources.skills:
                if not source.enabled:
                    continue
                try:
                    reports.append(inspect_skill_source_summary(source))
                except (OSError, RuntimeError, ValueError):
                    reports.append({"id": source.id, "type": source.type, "available": False, "count": 0})
            return reports

        return self._cached("skill-source-summaries", 60.0, load)

    def skills(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        def load() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            skills: list[dict[str, Any]] = []
            reports: list[dict[str, Any]] = []
            for source in self.config.sources.skills:
                if not source.enabled:
                    continue
                try:
                    packages, _ = inspect_skill_source(source)
                except (OSError, RuntimeError, ValueError):
                    reports.append({"id": source.id, "type": source.type, "available": False})
                    continue
                reports.append(
                    {
                        "id": source.id,
                        "type": source.type,
                        "available": True,
                        "state": "content-only" if source.type == "hermes" else "configured",
                        "count": len(packages),
                    }
                )
                for package in packages:
                    skills.append(
                        {
                            **package.catalog_summary(),
                            "source": package.source_id,
                            "source_type": package.source_type,
                            "provenance": package.provenance,
                        }
                    )
            skills.sort(key=lambda item: (str(item["source"]), str(item.get("category") or ""), str(item["name"])))
            return skills, reports

        return self._cached("skills", 60.0, load)

    def candidates(self, *, limit: int = 100, **filters: Any) -> tuple[bool, list[dict[str, Any]]]:
        if self.candidate_store is not None:
            return True, [
                {
                    "id": candidate.id,
                    "title": candidate.title,
                    "status": candidate.promotion.state,
                    "recurrence_count": candidate.problem.recurrence_count,
                    "promotion_reason": candidate.promotion.rationale,
                    "structured": True,
                }
                for candidate in self.candidate_store.query(limit=limit, **filters)
            ]
        source = self.config.sources.tooling_registry
        if source is None or not source.enabled:
            return False, []
        return True, [
            {**candidate.summary(), "structured": False}
            for candidate in ToolingRegistry(source.path).candidates()
        ]

    def candidate_count(self, **filters: Any) -> int:
        if self.candidate_store is not None:
            return self.candidate_store.count(**filters)
        # Legacy registry is content-only, never a writable Candidate authority.
        return len(self.candidates()[1])

    def candidate(self, candidate_id: str) -> dict[str, Any]:
        if self.candidate_store is None:
            raise ValueError("structured candidate store is not configured")
        return self.candidate_store.get(candidate_id).model_dump(mode="json")

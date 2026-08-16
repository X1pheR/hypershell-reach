from __future__ import annotations

from typing import Any

from .config import HATSConfig
from .managed_tools import load_tool_registry
from .runs import RunStore
from .skills import inspect_skill_source, inspect_skill_source_summary
from .tasks import TaskStore
from .tooling_registry import ToolingRegistry


class HATSReadModel:
    def __init__(self, config: HATSConfig) -> None:
        self.config = config
        self.runs = RunStore(config.workspace.runs, read_only=True)
        self.tasks = TaskStore(config.workspace.tasks, config.workspace.trash, read_only=True)

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
        return [script.summary() for script in load_tool_registry(self.config.sources.tools).list()]

    def run_summaries(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [record.summary() for record in self.runs.list(limit=limit)]

    def recent_run_summaries(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return [record.summary() for record in self.runs.recent(limit=limit)]

    def task_summaries(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [record.summary() for record in self.tasks.list(limit=limit)]

    def skill_source_summaries(self) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for source in self.config.sources.skills:
            if not source.enabled:
                continue
            try:
                reports.append(inspect_skill_source_summary(source))
            except (OSError, RuntimeError, ValueError):
                reports.append({"id": source.id, "type": source.type, "available": False, "count": 0})
        return reports

    def skills(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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

    def candidates(self) -> tuple[bool, list[dict[str, Any]]]:
        source = self.config.sources.tooling_registry
        if source is None or not source.enabled:
            return False, []
        return True, [candidate.summary() for candidate in ToolingRegistry(source.path).candidates()]

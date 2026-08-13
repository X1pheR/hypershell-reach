from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

ToolingStatus = Literal["observed", "guarded", "automated", "retired"]
PromotionState = Literal["candidate", "blocked", "not-warranted", "implemented"]

_HEADING = re.compile(r"^### (?P<id>[A-Z][A-Z0-9-]{1,63}) — (?P<title>\S.*)$")
_FIELD = re.compile(r"^- \*\*(?P<name>[^*]+):\*\*\s*(?P<value>.*)$")
_VALID_STATUSES = {"observed", "guarded", "automated", "retired"}
_VALID_PROMOTIONS = {"candidate", "blocked", "not-warranted", "implemented"}


class ToolingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    status: ToolingStatus
    promotion: Literal["candidate"] = "candidate"
    promotion_reason: str
    helper: str

    def summary(self) -> dict[str, str]:
        return self.model_dump()


class ToolingRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def candidates(self) -> list[ToolingCandidate]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ValueError(f"tooling registry not found: {self.path}") from exc
        except (IsADirectoryError, PermissionError, UnicodeError) as exc:
            raise ValueError(f"tooling registry is not readable: {self.path}") from exc

        candidates: list[ToolingCandidate] = []
        seen: set[str] = set()
        current_id: str | None = None
        current_title: str | None = None
        fields: dict[str, str] = {}

        def finish_entry() -> None:
            nonlocal fields, current_id, current_title
            if current_id is None or current_title is None:
                return
            if current_id in seen:
                raise ValueError(f"duplicate tooling registry entry: {current_id}")
            seen.add(current_id)

            status = fields.get("Status")
            if status is None:
                raise ValueError(f"tooling registry entry missing Status: {current_id}")
            if status not in _VALID_STATUSES:
                raise ValueError(f"unsupported tooling status for {current_id}: {status}")

            promotion = fields.get("Promotion")
            if promotion is None:
                fields = {}
                return
            if promotion not in _VALID_PROMOTIONS:
                raise ValueError(f"unsupported promotion state for {current_id}: {promotion}")
            if promotion != "candidate":
                fields = {}
                return
            if status not in {"observed", "guarded"}:
                raise ValueError(
                    f"candidate promotion requires observed or guarded status: {current_id}"
                )

            reason = fields.get("Promotion reason", "").strip()
            helper = fields.get("Helper candidate or implementation", "").strip()
            if not reason or not helper:
                raise ValueError(
                    f"candidate promotion requires Promotion reason and Helper candidate or implementation: {current_id}"
                )
            candidates.append(
                ToolingCandidate(
                    id=current_id,
                    title=current_title,
                    status=status,
                    promotion_reason=reason,
                    helper=helper,
                )
            )
            fields = {}

        fence_marker: str | None = None
        for raw_line in text.splitlines():
            stripped = raw_line.lstrip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                marker = stripped[:3]
                if fence_marker is None:
                    fence_marker = marker
                elif marker == fence_marker:
                    fence_marker = None
                continue
            if fence_marker is not None:
                continue

            heading = _HEADING.match(raw_line)
            if heading:
                finish_entry()
                current_id = heading.group("id")
                current_title = heading.group("title").strip()
                fields = {}
                continue
            if current_id is None:
                continue
            field = _FIELD.match(raw_line)
            if field:
                fields[field.group("name").strip()] = field.group("value").strip()

        finish_entry()
        return candidates

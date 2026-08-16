from __future__ import annotations

import pytest

from hats_mcp.tooling_registry import ToolingRegistry


def _registry(tmp_path):
    path = tmp_path / "tooling-registry.md"
    path.write_text(
        """# Tooling Failure Registry

### ATR-001 — Repeated selector ambiguity

- **Status:** guarded
- **Promotion:** candidate
- **Promotion reason:** The failure is recurring and has deterministic validation.
- **Helper candidate or implementation:** Shared selector linting.

### ATR-002 — Existing automated fix

- **Status:** automated
- **Promotion:** implemented
- **Promotion reason:** The canonical helper already owns this path.
- **Helper candidate or implementation:** `filesystem.compare-modes`.

### ATR-003 — One-off mistake

- **Status:** guarded
- **Promotion:** not-warranted
- **Promotion reason:** The sequencing rule is sufficient.
- **Helper candidate or implementation:** No helper is warranted.
""",
        encoding="utf-8",
    )
    return path


def test_candidates_are_derived_from_registry_entries(tmp_path) -> None:
    registry = ToolingRegistry(_registry(tmp_path))

    candidates = registry.candidates()

    assert [entry.id for entry in candidates] == ["ATR-001"]
    assert candidates[0].title == "Repeated selector ambiguity"
    assert candidates[0].status == "guarded"
    assert candidates[0].promotion == "candidate"
    assert candidates[0].promotion_reason.startswith("The failure is recurring")
    assert candidates[0].helper == "Shared selector linting."


def test_registry_rejects_candidate_without_required_review_fields(tmp_path) -> None:
    path = tmp_path / "tooling-registry.md"
    path.write_text(
        """### ATR-001 — Missing review
- **Status:** guarded
- **Promotion:** candidate
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="candidate promotion requires"):
        ToolingRegistry(path).candidates()


def test_registry_rejects_duplicate_entry_ids(tmp_path) -> None:
    path = tmp_path / "tooling-registry.md"
    path.write_text(
        """### ATR-001 — First
- **Status:** guarded
- **Promotion:** not-warranted
- **Promotion reason:** Documentation is sufficient.
- **Helper candidate or implementation:** None.

### ATR-001 — Duplicate
- **Status:** observed
- **Promotion:** candidate
- **Promotion reason:** Repeated.
- **Helper candidate or implementation:** Helper.
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate tooling registry entry"):
        ToolingRegistry(path).candidates()


def test_registry_rejects_unknown_promotion_state(tmp_path) -> None:
    path = tmp_path / "tooling-registry.md"
    path.write_text(
        """### ATR-001 — Unknown state
- **Status:** guarded
- **Promotion:** maybe
- **Promotion reason:** Unknown.
- **Helper candidate or implementation:** Unknown.
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported promotion state"):
        ToolingRegistry(path).candidates()


def test_registry_ignores_entry_templates_inside_fenced_code(tmp_path) -> None:
    path = tmp_path / "tooling-registry.md"
    path.write_text(
        """# Registry

```markdown
### ATR-NNN — Short failure name
- **Status:** observed|guarded|automated|retired
- **Promotion:** candidate|blocked|not-warranted|implemented
```

### ATR-022 — Real candidate
- **Status:** guarded
- **Promotion:** candidate
- **Promotion reason:** Repeated and deterministic.
- **Helper candidate or implementation:** OIDC preflight.
""",
        encoding="utf-8",
    )

    candidates = ToolingRegistry(path).candidates()

    assert [entry.id for entry in candidates] == ["ATR-022"]


def test_registry_accepts_automated_promotion_as_non_candidate(tmp_path) -> None:
    path = tmp_path / "tooling-registry.md"
    path.write_text(
        """### ATR-033 — Automated helper
- **Status:** automated
- **Promotion:** automated
- **Promotion reason:** The helper is implemented and live.
- **Helper candidate or implementation:** github.repository-lifecycle.

### ATR-034 — Remaining candidate
- **Status:** guarded
- **Promotion:** candidate
- **Promotion reason:** Repeated and deterministic.
- **Helper candidate or implementation:** example.helper.
""",
        encoding="utf-8",
    )

    candidates = ToolingRegistry(path).candidates()

    assert [entry.id for entry in candidates] == ["ATR-034"]

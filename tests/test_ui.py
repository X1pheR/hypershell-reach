from __future__ import annotations

import re
from pathlib import Path

from starlette.testclient import TestClient

from hats_mcp.config import HATSConfig
from hats_mcp.runs import RunStore
from hats_mcp.tasks import TaskStore
from hats_mcp.ui import create_app
from hats_mcp.ui_docs import DocumentationPage, render_document


def _config(tmp_path: Path) -> HATSConfig:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "inspect.py").write_text(
        """# ---\n# id: system.inspect\n# name: Inspect system\n# description: Read safe system metadata.\n# domain: system\n# interpreter: python3\n# requires: [linux]\n# mutating: false\n# idempotent: true\n# ---\nprint('ok')\n""",
        encoding="utf-8",
    )
    skills = tmp_path / "skills" / "example"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example skill.\n---\n# Example\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.md"
    registry.write_text(
        """### ATR-999 — Example gap\n- **Status:** observed\n- **Promotion:** candidate\n- **Promotion reason:** Repeated bounded gap.\n- **Helper candidate or implementation:** example.inspect\n""",
        encoding="utf-8",
    )
    return HATSConfig.model_validate(
        {
            "schema_version": 1,
            "workspace": {
                "tmp": str(tmp_path / "tmp"),
                "runs": str(tmp_path / "runs"),
                "tasks": str(tmp_path / "tasks"),
                "trash": str(tmp_path / "trash"),
            },
            "sources": {
                "tools": [{"id": "local", "path": str(tmp_path / "tools")}],
                "skills": [{"id": "local", "path": str(tmp_path / "skills")}],
                "tooling_registry": {"path": str(registry)},
            },
            "targets": {
                "docker": {
                    "display_name": "Docker host",
                    "capabilities": ["linux", "docker"],
                    "ssh": {
                        "host": "192.0.2.55",
                        "user": "operator",
                        "identity_file": "/run/secrets/id_ed25519",
                        "known_hosts_file": "/run/secrets/known_hosts",
                    },
                }
            },
        }
    )


def _client(tmp_path: Path) -> TestClient:
    config = _config(tmp_path)
    run_store = RunStore(config.workspace.runs)
    run = run_store.create(
        operation="run_command",
        target="docker",
        timeout_seconds=30,
        may_mutate=False,
    )
    run_store.finish(
        run.id,
        {
            "status": "succeeded",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 2,
            "stdout": {"bytes": 0, "truncated": False},
            "stderr": {"bytes": 0, "truncated": False},
        },
    )
    TaskStore(config.workspace.tasks, config.workspace.trash).create(
        title="Example task",
        objective="Private objective must not be rendered in the list view.",
    )
    return TestClient(create_app(config))


def _primary_navigation(html: str) -> str:
    match = re.search(r'<nav class="primary-navigation"[^>]*>(.*?)</nav>', html, flags=re.DOTALL)
    assert match is not None
    return match.group(1)


def test_ui_exposes_canonical_application_shell_and_primary_navigation(tmp_path) -> None:
    client = _client(tmp_path)

    home = client.get("/")
    assert home.status_code == 200
    navigation = _primary_navigation(home.text)
    for label in ("Overview", "Targets", "Tooling", "Runs", "Tasks", "Skills"):
        assert f">{label}</a>" in navigation
    assert ">Documentation</a>" not in navigation
    assert "Tooling Candidates" not in navigation
    assert 'href="/help" aria-label="Help"' in home.text
    assert 'class="skip-link" href="#main-content"' in home.text
    assert 'id="main-content" tabindex="-1"' in home.text
    assert 'id="mobile-menu-toggle"' in home.text
    assert 'aria-controls="mobile-navigation"' in home.text
    assert 'id="mobile-navigation"' in home.text
    assert 'class="brand-mark" src="/assets/hats-mark.svg"' in home.text
    assert '<link rel="icon" href="/assets/hats-mark.svg" type="image/svg+xml">' in home.text
    assert "Read-only" in home.text
    assert client.get("/healthz").json() == {"status": "ok", "role": "hats-ui"}
    assert client.post("/targets").status_code == 405


def test_tooling_groups_managed_tools_and_candidates(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/tooling")
    assert response.status_code == 200
    assert "Managed tools" in response.text
    assert "system.inspect" in response.text
    assert 'id="candidates"' in response.text
    assert "Tooling candidates" in response.text
    assert "ATR-999" in response.text
    assert "Repeated bounded gap." in response.text

    compatibility = client.get("/candidates", follow_redirects=False)
    assert compatibility.status_code in {302, 307, 308}
    assert compatibility.headers["location"] == "/tooling#candidates"


def test_help_exposes_user_and_curated_technical_reference_with_docs_compatibility(tmp_path) -> None:
    client = _client(tmp_path)

    user_guide = client.get("/help")
    assert user_guide.status_code == 200
    assert "Help" in user_guide.text
    assert "Technical reference" in user_guide.text
    assert "What HATS is" in user_guide.text
    assert "Managed tools" in user_guide.text
    assert "Tooling candidates" in user_guide.text

    architecture = client.get("/help/technical/architecture")
    assert architecture.status_code == 200
    assert "Architecture" in architecture.text
    assert "Overview" in architecture.text
    assert "On this page" in architecture.text

    configuration = client.get("/help/technical/configuration")
    assert configuration.status_code == 200
    assert 'href="/help/technical/skills"' in configuration.text

    assert client.get("/help/technical/not-a-document").status_code == 404
    docs = client.get("/docs", follow_redirects=False)
    assert docs.status_code in {302, 307, 308}
    assert docs.headers["location"] == "/help"
    technical = client.get("/docs/technical/skills", follow_redirects=False)
    assert technical.status_code in {302, 307, 308}
    assert technical.headers["location"] == "/help/technical/skills"
    assert client.post("/docs").status_code == 405


def test_documentation_renderer_escapes_raw_html_and_rejects_javascript_links(tmp_path, monkeypatch) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "security-test.md").write_text(
        "# Security test\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("hats_mcp.ui_docs._docs_root", lambda: docs)
    page = DocumentationPage(
        slug="security-test",
        title="Security test",
        description="Renderer safety test.",
        filename="security-test.md",
        group="Test",
    )

    rendered = render_document(page)

    assert "<script>" not in rendered.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered.html
    assert 'href="javascript:' not in rendered.html


def test_targets_view_does_not_render_connection_or_credential_fields(tmp_path) -> None:
    client = _client(tmp_path)
    response = client.get("/targets")

    assert response.status_code == 200
    assert "Docker host" in response.text
    assert "docker" in response.text
    assert "192.0.2.55" not in response.text
    assert "operator" not in response.text
    assert "/run/secrets/" not in response.text


def test_views_render_existing_domain_summaries_without_private_task_content(tmp_path) -> None:
    client = _client(tmp_path)

    assert "run_command" in client.get("/runs").text
    tasks = client.get("/tasks").text
    assert "Example task" in tasks
    assert "Private objective" not in tasks
    assert "Example skill." in client.get("/skills").text


def test_html_and_assets_use_defensive_headers_without_inline_script_policy(tmp_path) -> None:
    client = _client(tmp_path)
    response = client.get("/")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "style-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
    assert '<link rel="stylesheet" href="/assets/app.css">' in response.text
    assert '<script src="/assets/app.js" defer></script>' in response.text

    css = client.get("/assets/app.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert "@media" in css.text
    assert ".primary-navigation" in css.text

    javascript = client.get("/assets/app.js")
    assert javascript.status_code == 200
    assert "mobile-navigation" in javascript.text
    assert "Escape" in javascript.text

    mark = client.get("/assets/hats-mark.svg")
    assert mark.status_code == 200
    assert mark.headers["content-type"].startswith("image/svg+xml")
    assert 'aria-label="HATS product mark"' in mark.text
    assert mark.text.count("<circle") == 3
    assert "#ff2093" in mark.text
    assert "#3c6cfe" in mark.text
    assert "#67e8f9" in mark.text


def test_skills_view_distinguishes_unavailable_source_from_empty(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)

    def unavailable(*_args, **_kwargs):
        raise PermissionError("source cannot be read")

    monkeypatch.setattr("hats_mcp.read_model.inspect_skill_source", unavailable)
    response = client.get("/skills")

    assert response.status_code == 200
    assert "Skill source unavailable" in response.text
    assert "local" in response.text
    assert "No entries." not in response.text


def test_overview_surfaces_glanceable_live_state(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "1/1 enabled" in response.text
    assert "1 managed tool" in response.text
    assert "1 recent run" in response.text
    assert "1 active task" in response.text
    assert "1 skill" in response.text
    assert "1 tooling candidate" in response.text


def test_documentation_uses_collapsible_mobile_navigation(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/help")

    assert response.status_code == 200
    assert 'class="docs-navigation docs-navigation-mobile"' in response.text
    assert "<summary>Help menu</summary>" in response.text
    css = client.get("/assets/app.css").text
    assert ".docs-navigation-mobile" in css


def test_overview_uses_lightweight_summaries_instead_of_full_detail_loaders(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)

    def detail_loader_used(*_args, **_kwargs):
        raise RuntimeError("detail loader should not be used by overview")

    monkeypatch.setattr("hats_mcp.read_model.HATSReadModel.run_summaries", detail_loader_used)
    monkeypatch.setattr("hats_mcp.read_model.HATSReadModel.skills", detail_loader_used)

    response = client.get("/")

    assert response.status_code == 200
    assert "1 recent run" in response.text
    assert "1 skill" in response.text
    assert "Unavailable" not in response.text


def test_growing_views_use_server_rendered_discovery_controls_and_filtered_empty_state(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    run_rows = [
        {
            "id": f"run-{index:03d}",
            "status": "failed" if index == 0 else "succeeded",
            "operation": "run_command" if index % 2 == 0 else "run_script",
            "target": "docker",
            "started_at": f"2026-08-20T12:{index:02d}:00Z",
            "ended_at": f"2026-08-20T12:{index:02d}:01Z",
            "task_id": None,
            "script_id": "system.inspect" if index % 2 else None,
            "ambiguous": False,
            "retained": False,
        }
        for index in range(30)
    ]
    monkeypatch.setattr("hats_mcp.read_model.HATSReadModel.run_summaries", lambda self, limit=100: run_rows)
    client = TestClient(create_app(config))

    first = client.get("/runs")
    assert first.status_code == 200
    assert 'class="table-controls"' in first.text
    assert 'name="q"' in first.text
    assert 'name="filter"' in first.text
    assert 'aria-sort="descending"' in first.text
    assert "30 results · showing 1–25" in first.text
    assert "Page 1 of 2" in first.text

    second = client.get("/runs?page=2")
    assert "30 results · showing 26–30" in second.text
    assert "Page 2 of 2" in second.text

    filtered = client.get("/runs?q=run-000&filter=failed")
    assert "1 result · showing 1–1" in filtered.text
    assert "run-000" in filtered.text
    assert "run-001" not in filtered.text

    missing = client.get("/runs?q=does-not-exist")
    assert "No entries match the current filters." in missing.text

    skills = client.get("/skills?q=missing-skill")
    assert 'class="table-controls"' in skills.text
    assert "No entries match the current filters." in skills.text

    tooling = client.get("/tooling?tools_q=Inspect&candidates_filter=observed")
    assert 'name="tools_q"' in tooling.text
    assert 'name="candidates_filter"' in tooling.text
    assert "Inspect system" in tooling.text
    assert "Example gap" in tooling.text


def test_shell_separates_runtime_availability_from_read_only_mode_and_contextual_help(tmp_path) -> None:
    client = _client(tmp_path)
    response = client.get("/runs")
    assert response.status_code == 200
    assert 'aria-label="Runtime availability: Available">Runtime available</span>' in response.text
    assert 'aria-label="Mode: Read-only">Read-only</span>' in response.text
    assert 'aria-label="About this page"' in response.text
    assert 'href="/help/technical/runs-and-tasks"' in response.text


from hats_mcp.candidates import (
    CandidateOwnership,
    CandidateProblem,
    CandidateProposal,
    CandidateReference,
    CandidateStore,
)


def _wp6_detail_client(tmp_path: Path) -> tuple[TestClient, str, str, str, str]:
    config = _config(tmp_path)
    payload = config.model_dump()
    payload["workspace"]["candidates"] = str(tmp_path / "candidates")
    config = HATSConfig.model_validate(payload)

    task = TaskStore(config.workspace.tasks, config.workspace.trash).create(
        title="WP6 continuity fixture",
        objective="Render the complete safe Task continuity record.",
        project_ref="projects/example.md",
        next_action="Accept the read-only detail views.",
        continuity={
            "authorization": "Read-only UI acceptance only.",
            "sources": [
                {
                    "classification": "configured",
                    "reference": "config/example.yaml",
                    "purpose": "Canonical fixture state.",
                }
            ],
            "completed": ["Implemented detail views."],
            "validation": ["Unit acceptance pending."],
            "cleanup": ["No temporary runtime resources retained."],
            "recovery": "Remove the isolated fixture if acceptance fails.",
            "blockers": ["None."],
            "assumptions": [
                {
                    "statement": "The browser remains read-only.",
                    "evidence_class": "configured",
                    "impact_if_wrong": "high",
                    "decision": "Reject WP6 if mutation surfaces appear.",
                }
            ],
        },
    )

    run_store = RunStore(config.workspace.runs)
    run = run_store.create(
        operation="run_script",
        target="docker",
        timeout_seconds=30,
        may_mutate=False,
        idempotent=True,
        purpose="Verify the WP6 detail relationship without exposing execution content.",
        task_id=task.id,
        script_id="system.inspect",
        script_source="local",
        script_sha256="a" * 64,
        argument_names=["example"],
    )
    run_store.finish(
        run.id,
        {
            "status": "succeeded",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 9,
            "stdout": {"text": "SECRET-STDOUT-MUST-NOT-RENDER", "bytes": 29, "truncated": False},
            "stderr": {"text": "SECRET-STDERR-MUST-NOT-RENDER", "bytes": 29, "truncated": False},
        },
    )
    historical = run_store.create(
        operation="run_command",
        target="docker",
        timeout_seconds=30,
        may_mutate=False,
    )
    run_store.finish(
        historical.id,
        {
            "status": "succeeded",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 3,
            "stdout": {"bytes": 0, "truncated": False},
            "stderr": {"bytes": 0, "truncated": False},
        },
    )

    candidates = CandidateStore(config.workspace.candidates)
    candidate = candidates.create(
        candidate_id="ATR-999",
        title="WP6 structured candidate",
        problem=CandidateProblem.model_validate(
            {
                "summary": "Repeated UI acceptance needs explicit provenance.",
                "cause": "List-only views hide the decision context.",
                "recurrence": "The same context is needed across Task, Run and Candidate reviews.",
                "evidence": ["WP6 product acceptance requires exact provenance navigation."],
            }
        ),
        proposal=CandidateProposal.model_validate(
            {
                "capability": "Render read-only provenance detail with exact relationships.",
                "proposed_tool_id": "system.inspect",
                "required_inputs": [{"name": "record_id", "description": "Stable record identifier."}],
                "expected_outputs": [{"name": "detail", "description": "Safe provenance detail view."}],
                "safety": {
                    "mutating": False,
                    "secret_access": False,
                    "boundary": "Read persisted safe metadata only; never render raw execution content.",
                },
                "acceptance": ["Task, Run and managed Tool references resolve to exact read-only detail pages."],
            }
        ),
        ownership=CandidateOwnership(owner_id="X1pheR/homelab-agent-tooling-skills-mcp"),
        promotion_rationale="The bounded relationship view is reusable product behavior.",
    )
    candidate = candidates.transition(
        candidate.id,
        expected_revision=candidate.revision,
        target_state="approved",
        state_reason="Approved fixture state.",
    )
    candidate = candidates.link_task(
        candidate.id,
        expected_revision=candidate.revision,
        task_id=task.id,
    )
    candidate = candidates.transition(
        candidate.id,
        expected_revision=candidate.revision,
        target_state="implemented",
        state_reason="Accepted fixture implementation.",
        final_reference=CandidateReference(kind="managed-tool", id="system.inspect"),
    )
    return TestClient(create_app(config)), task.id, run.id, historical.id, candidate.id


def test_wp6_task_and_run_details_use_exact_relationships_and_safe_diagnostics(tmp_path) -> None:
    client, task_id, run_id, historical_id, _ = _wp6_detail_client(tmp_path)

    task = client.get(f"/tasks/{task_id}")
    assert task.status_code == 200
    for expected in (
        "Render the complete safe Task continuity record.",
        "Read-only UI acceptance only.",
        "Canonical fixture state.",
        "Implemented detail views.",
        "Unit acceptance pending.",
        "No temporary runtime resources retained.",
        "Remove the isolated fixture if acceptance fails.",
        "The browser remains read-only.",
        "Related Runs",
    ):
        assert expected in task.text
    assert f'href="/runs/{run_id}"' in task.text
    assert task.text.index("Verify the WP6 detail relationship") < task.text.index("run_script")

    run = client.get(f"/runs/{run_id}")
    assert run.status_code == 200
    assert "Verify the WP6 detail relationship without exposing execution content." in run.text
    assert f'href="/tasks/{task_id}"' in run.text
    assert 'href="/tooling/system.inspect"' in run.text
    assert "Execution succeeded with exit_code=0." in run.text
    assert "Stdout bytes" in run.text
    assert "Stderr bytes" in run.text
    assert "SECRET-STDOUT-MUST-NOT-RENDER" not in run.text
    assert "SECRET-STDERR-MUST-NOT-RENDER" not in run.text
    assert "argument_names" not in run.text

    historical = client.get(f"/runs/{historical_id}")
    assert historical.status_code == 200
    assert "Purpose is unavailable for this historical run." in historical.text


def test_wp6_candidate_detail_renders_contract_and_exact_task_tool_links(tmp_path) -> None:
    client, task_id, _, _, candidate_id = _wp6_detail_client(tmp_path)

    listing = client.get("/tooling")
    assert listing.status_code == 200
    assert f'href="/candidates/{candidate_id}"' in listing.text

    response = client.get(f"/candidates/{candidate_id}")
    assert response.status_code == 200
    for expected in (
        "Repeated UI acceptance needs explicit provenance.",
        "List-only views hide the decision context.",
        "Render read-only provenance detail with exact relationships.",
        "Read persisted safe metadata only; never render raw execution content.",
        "X1pheR/homelab-agent-tooling-skills-mcp",
        "Acceptance contract",
        "Task, Run and managed Tool references resolve to exact read-only detail pages.",
    ):
        assert expected in response.text
    assert f'href="/tasks/{task_id}"' in response.text
    assert 'href="/tooling/system.inspect"' in response.text

    tool = client.get("/tooling/system.inspect")
    assert tool.status_code == 200
    assert "Inspect system" in tool.text
    assert "inspect.py" not in tool.text


def test_wp6_task_run_candidate_browser_surfaces_are_get_only(tmp_path) -> None:
    client, task_id, run_id, _, candidate_id = _wp6_detail_client(tmp_path)
    paths = (f"/tasks/{task_id}", f"/runs/{run_id}", f"/candidates/{candidate_id}")

    for path in paths:
        assert client.get(path).status_code == 200
        for method in (client.post, client.put, client.patch, client.delete):
            assert method(path).status_code == 405

    route_root = tmp_path / "route-contract"
    route_root.mkdir()
    app = create_app(_config(route_root))
    protected = [
        route for route in app.routes
        if route.path in {"/tasks", "/tasks/{task_id}", "/runs", "/runs/{run_id}", "/candidates/{candidate_id}"}
    ]
    assert protected
    assert all(route.methods <= {"GET", "HEAD"} for route in protected)


def test_wp6_structured_candidate_detail_fails_closed_when_store_is_not_configured(tmp_path) -> None:
    client = _client(tmp_path)
    response = client.get("/candidates/ATR-999")

    assert response.status_code == 404
    assert response.text == "Structured Candidate detail is not available."

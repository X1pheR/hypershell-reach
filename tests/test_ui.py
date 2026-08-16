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
    for label in ("Overview", "Targets", "Tooling", "Runs", "Tasks", "Skills", "Documentation"):
        assert f">{label}</a>" in navigation
    assert "Tooling Candidates" not in navigation
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


def test_documentation_exposes_user_and_curated_technical_docs(tmp_path) -> None:
    client = _client(tmp_path)

    user_guide = client.get("/docs")
    assert user_guide.status_code == 200
    assert "User guide" in user_guide.text
    assert "Technical documentation" in user_guide.text
    assert "What HATS is" in user_guide.text
    assert "Managed tools" in user_guide.text
    assert "Tooling candidates" in user_guide.text

    architecture = client.get("/docs/technical/architecture")
    assert architecture.status_code == 200
    assert "Architecture" in architecture.text
    assert "Overview" in architecture.text
    assert "On this page" in architecture.text

    configuration = client.get("/docs/technical/configuration")
    assert configuration.status_code == 200
    assert 'href="/docs/technical/skills"' in configuration.text

    assert client.get("/docs/technical/not-a-document").status_code == 404
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

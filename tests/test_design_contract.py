from pathlib import Path


ROOT = Path(__file__).parents[1]
DESIGN = ROOT / "DESIGN.md"
UI_ASSETS = (ROOT / "src" / "hypershell_reach" / "ui_assets.py").read_text(encoding="utf-8").lower()

CANONICAL_COLORS = {
    "page": "#050816",
    "surface": "#0b1020",
    "surface-raised": "#0f172a",
    "surface-soft": "#131d31",
    "text": "#e2e8f0",
    "heading": "#f1f5f9",
    "muted": "#94a3b8",
    "border": "#4c5c80",
    "primary": "#3c6cfe",
    "secondary": "#ff2093",
    "tertiary": "#22d3ee",
    "focus": "#67e8f9",
    "structural-cyan": "#78aeb9",
    "structural-pink": "#b97599",
    "success": "#6ee7a8",
    "warning": "#f3c96b",
    "error": "#ff8585",
    "scrollbar-track": "#070b17",
    "scrollbar-thumb": "#33415f",
    "scrollbar-thumb-hover": "#506284",
}


def test_repository_design_projects_canonical_hypershell_family() -> None:
    text = DESIGN.read_text(encoding="utf-8")
    assert text.startswith("---\nversion: alpha\n")
    assert "_meta/operating-models/web-ui/DESIGN.md" in text
    assert "small, read-only, server-rendered" in text
    assert "Read-only" in text and "runtime" in text
    assert "SPA" in text
    for token, value in CANONICAL_COLORS.items():
        assert f'{token}: "{value.upper()}"'.lower() in text.lower()


def test_runtime_css_keeps_canonical_shared_core_palette() -> None:
    css_names = {
        "primary": "accent",
        "structural-cyan": "structural-cyan",
        "structural-pink": "structural-pink",
        "error": "danger",
    }
    for token, value in CANONICAL_COLORS.items():
        css_token = css_names.get(token, token)
        assert f"--{css_token}: {value};" in UI_ASSETS


def test_shell_uses_family_brand_and_neutral_read_only_mode_badge() -> None:
    ui = (ROOT / "src" / "hypershell_reach" / "ui.py").read_text(encoding="utf-8")
    assert '<span class="brand-copy"><strong>Hypershell</strong><small>Hypershell Reach</small></span>' in ui
    assert 'class="mode-badge" aria-label="Mode: Read-only">Read-only</span>' in ui
    assert 'utility-dot' not in ui


def test_help_is_utility_destination_and_runtime_availability_is_not_mode() -> None:
    ui = (ROOT / "src" / "hypershell_reach" / "ui.py").read_text(encoding="utf-8")
    assert '("Documentation", "/docs")' not in ui
    assert 'href="/help"' in ui
    assert 'aria-label="Help"' in ui
    assert 'class="availability-badge" aria-label="Runtime availability: Available"' in ui
    assert 'class="mode-badge" aria-label="Mode: Read-only"' in ui
    assert 'Route("/help"' in ui
    assert 'Route("/docs"' in ui and 'RedirectResponse("/help"' in ui


def test_growing_read_only_tables_have_server_rendered_discovery_controls() -> None:
    ui = (ROOT / "src" / "hypershell_reach" / "ui.py").read_text(encoding="utf-8")
    for marker in ("table-controls", "table-search", "table-filter", "table-pagination", "aria-sort"):
        assert marker in ui
    assert 'request.query_params' in ui
    assert 'page_size=25' in ui
    assert 'No entries match the current filters.' in ui
    assert '<table class="data-table">' in ui


def test_responsive_tables_preserve_values_as_semantic_mobile_records() -> None:
    css = UI_ASSETS
    assert '.data-table td::before' in css
    assert 'content: attr(data-label)' in css
    assert '.data-table tbody' in css
    assert '.data-table tr' in css


def test_filter_forms_preserve_strict_form_action_csp_via_same_origin_script_navigation() -> None:
    ui = (ROOT / "src" / "hypershell_reach" / "ui.py").read_text(encoding="utf-8")
    assets = (ROOT / "src" / "hypershell_reach" / "ui_assets.py").read_text(encoding="utf-8")
    assert "form-action 'none'" in ui
    assert 'document.querySelectorAll(".table-controls")' in assets
    assert 'event.preventDefault()' in assets
    assert 'new FormData(form)' in assets
    assert 'window.location.assign' in assets
    assert 'fetch(' not in assets


def test_application_tooltips_are_edge_safe_at_narrow_widths() -> None:
    css = UI_ASSETS
    assert '.header-actions [data-tooltip]::after, .contextual-help[data-tooltip]::after' in css
    assert 'max-width: min(12rem, calc(100vw - 1rem))' in css

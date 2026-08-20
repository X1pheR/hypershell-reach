from pathlib import Path


ROOT = Path(__file__).parents[1]
DESIGN = ROOT / "DESIGN.md"
UI_ASSETS = (ROOT / "src" / "hats_mcp" / "ui_assets.py").read_text(encoding="utf-8").lower()

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

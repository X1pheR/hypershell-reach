from pathlib import Path
import tomllib
import re


ROOT = Path(__file__).parents[1]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
BROWSER = (ROOT / "scripts" / "ci-browser.sh").read_text(encoding="utf-8")


def test_github_actions_are_pinned_to_full_commit_shas() -> None:
    for workflow in (CI, RELEASE):
        uses = re.findall(r"uses:\s*([^\s#]+)", workflow)
        assert uses
        for value in uses:
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", value), value


def test_release_write_permission_is_job_scoped() -> None:
    assert "permissions:\n  contents: read" in RELEASE
    release_job = RELEASE.split("  release:\n", 1)[1]
    assert "    permissions:\n      contents: write" in release_job


def test_browser_gate_is_reused_by_ci_and_release() -> None:
    invocation = "bash scripts/ci-browser.sh"
    assert invocation in CI
    assert invocation in RELEASE


def _locked_version(package_name: str) -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    matches = [
        package["version"]
        for package in lock["package"]
        if package.get("name") == package_name
    ]
    assert len(matches) == 1, package_name
    return matches[0]


def test_browser_container_matches_reviewed_playwright_runtime() -> None:
    assert "docker run --rm --init --ipc=host --network host" in BROWSER
    assert '"playwright==1.62.0"' in BROWSER
    assert "playwright/python@sha256:aa81288e738725378becba5b3e06cb0f3a7f012a610e87e8d767a090ea3f740d" in BROWSER
    assert '"axe-playwright-python==0.1.8"' in BROWSER
    for package_name in ("pytest", "pytest-asyncio", "pytest-playwright"):
        assert f'"{package_name}=={_locked_version(package_name)}"' in BROWSER

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
    assert "    permissions:\n      contents: write\n      packages: write" in release_job


def test_release_publishes_versioned_ghcr_image_and_digest_metadata() -> None:
    assert 'image="ghcr.io/${GITHUB_REPOSITORY,,}:${version}"' in RELEASE
    assert 'docker login ghcr.io' in RELEASE
    assert 'docker build \\' in RELEASE
    assert '--build-arg REACH_VERSION="${version}"' in RELEASE
    assert '--build-arg REACH_REVISION="${GITHUB_SHA}"' in RELEASE
    assert 'docker push "${image}"' in RELEASE
    assert 'IMAGE.txt' in RELEASE
    assert "\x01" not in RELEASE
    assert '"${push_output}" | sed -n' not in RELEASE


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
    assert 'docker network create "${NETWORK}"' in BROWSER
    assert 'BASE_URL="http://${APP_CONTAINER}:8080"' in BROWSER
    assert '--network "${NETWORK}"' in BROWSER
    assert "--network host" not in BROWSER
    assert '-v "${ROOT_DIR}:/src:ro"' not in BROWSER
    assert 'docker cp "${ROOT_DIR}/tests/test_browser.py"' in BROWSER
    assert '"playwright==1.62.0"' in BROWSER
    assert "playwright/python:v1.62.0-noble" in BROWSER
    assert '"axe-playwright-python==0.1.8"' in BROWSER
    for package_name in ("pytest", "pytest-asyncio", "pytest-playwright"):
        assert f'"{package_name}=={_locked_version(package_name)}"' in BROWSER


def test_browser_gate_prevents_parallel_local_runs() -> None:
    assert 'BROWSER_LOCK_FILE="${BROWSER_LOCK_FILE:-${TMPDIR:-/tmp}/reach-ci-browser.lock}"' in BROWSER
    assert 'flock -n 9' in BROWSER
    assert 'Another Hypershell Reach browser acceptance run is already active' in BROWSER


def test_browser_gate_has_a_hard_test_timeout() -> None:
    assert 'BROWSER_RUN_TIMEOUT_SECONDS="${BROWSER_RUN_TIMEOUT_SECONDS:-600}"' in BROWSER
    assert 'BROWSER_TEST_TIMEOUT_SECONDS="${BROWSER_TEST_TIMEOUT_SECONDS:-300}"' in BROWSER
    assert 'timeout --signal=TERM --kill-after=30s "${BROWSER_RUN_TIMEOUT_SECONDS}"' in BROWSER
    assert 'timeout --signal=TERM --kill-after=30s "${BROWSER_TEST_TIMEOUT_SECONDS}"' in BROWSER


def test_browser_results_are_isolated_per_run() -> None:
    assert 'BROWSER_RESULTS_DIR="${RESULTS_ROOT}/browser/${RUN_ID}"' in BROWSER
    assert 'APP_LOG="${BROWSER_RESULTS_DIR}/reach.log"' in BROWSER
    assert 'rm -rf "${BROWSER_RESULTS_DIR:?}"/*' not in BROWSER


def test_browser_runtime_uses_release_tag_not_digest() -> None:
    assert 'PLAYWRIGHT_IMAGE="mcr.microsoft.com/playwright/python:v1.62.0-noble"' in BROWSER
    assert 'playwright/python@sha256:' not in BROWSER


def test_browser_fixture_is_portable_to_non_root_reach_user() -> None:
    assert 'chmod -R a+rX "${FIXTURE_ROOT}"' in BROWSER
    assert '"${FIXTURE_ROOT}/tmp"' in BROWSER
    assert '"${FIXTURE_ROOT}/runs"' in BROWSER
    assert '"${FIXTURE_ROOT}/tasks"' in BROWSER
    assert '"${FIXTURE_ROOT}/trash"' in BROWSER
    assert '"${FIXTURE_ROOT}/candidates"' in BROWSER
    assert 'chmod -R a+rwX' in BROWSER

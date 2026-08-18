#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
RESULTS_ROOT="${BROWSER_RESULTS_ROOT:-${ROOT_DIR}/test-results}"
BROWSER_RESULTS_DIR="${RESULTS_ROOT}/browser"
APP_LOG="${RESULTS_ROOT}/hats-ui.log"
PLAYWRIGHT_IMAGE="mcr.microsoft.com/playwright/python@sha256:aa81288e738725378becba5b3e06cb0f3a7f012a610e87e8d767a090ea3f740d"
BASE_URL="http://127.0.0.1:18081"
FIXTURE_ROOT="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/hats-browser.XXXXXX")"

mkdir -p "${BROWSER_RESULTS_DIR}"
rm -rf "${BROWSER_RESULTS_DIR:?}"/*
mkdir -p "${FIXTURE_ROOT}/tmp" "${FIXTURE_ROOT}/runs" "${FIXTURE_ROOT}/tasks" \
  "${FIXTURE_ROOT}/trash" "${FIXTURE_ROOT}/tools" "${FIXTURE_ROOT}/skills/example"

cat > "${FIXTURE_ROOT}/tools/inspect.py" <<'PY'
# ---
# id: system.inspect
# name: Inspect system
# description: Read safe system metadata.
# domain: system
# interpreter: python3
# requires: [linux]
# mutating: false
# idempotent: true
# ---
print("ok")
PY

cat > "${FIXTURE_ROOT}/skills/example/SKILL.md" <<'MD'
---
name: example
description: Example browser fixture skill.
---
# Example
MD

cat > "${FIXTURE_ROOT}/registry.md" <<'MD'
### ATR-999 — Browser fixture
- **Status:** observed
- **Promotion:** candidate
- **Promotion reason:** Browser acceptance fixture.
- **Helper candidate or implementation:** example.inspect
MD

cat > "${FIXTURE_ROOT}/config.yaml" <<YAML
schema_version: 1
workspace:
  tmp: ${FIXTURE_ROOT}/tmp
  runs: ${FIXTURE_ROOT}/runs
  tasks: ${FIXTURE_ROOT}/tasks
  trash: ${FIXTURE_ROOT}/trash
sources:
  tools:
    - id: local
      path: ${FIXTURE_ROOT}/tools
  skills:
    - id: local
      path: ${FIXTURE_ROOT}/skills
  tooling_registry:
    path: ${FIXTURE_ROOT}/registry.md
targets:
  docker:
    display_name: Browser fixture
    capabilities: [linux, docker]
    ssh:
      host: 192.0.2.55
      user: operator
      identity_file: /run/secrets/unused_identity
      known_hosts_file: /run/secrets/unused_known_hosts
YAML

app_pid=""
cleanup() {
  if [[ -n "${app_pid}" ]]; then
    kill "${app_pid}" >/dev/null 2>&1 || true
    wait "${app_pid}" >/dev/null 2>&1 || true
  fi
  rm -rf "${FIXTURE_ROOT}"
}
trap cleanup EXIT

uv run --frozen --extra dev hats-ui \
  --config "${FIXTURE_ROOT}/config.yaml" \
  --host 127.0.0.1 \
  --port 18081 > "${APP_LOG}" 2>&1 &
app_pid=$!

for attempt in $(seq 1 30); do
  if curl --fail --silent "${BASE_URL}/healthz" >/dev/null; then
    break
  fi
  if [[ "${attempt}" -eq 30 ]]; then
    echo "HATS UI did not become ready" >&2
    exit 1
  fi
  sleep 1
done

docker run --rm --init --ipc=host --network host \
  -e RUN_BROWSER_TESTS=1 \
  -e HATS_BROWSER_BASE_URL="${BASE_URL}" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTEST_ADDOPTS='-p no:cacheprovider' \
  -v "${ROOT_DIR}:/src:ro" \
  -v "${BROWSER_RESULTS_DIR}:/test-results/browser" \
  -w /src \
  "${PLAYWRIGHT_IMAGE}" \
  bash -lc 'python -m pip install --disable-pip-version-check -q \
    "playwright==1.62.0" \
    "pytest==8.4.2" \
    "pytest-asyncio==0.26.0" \
    "pytest-playwright==0.9.0" \
    "axe-playwright-python==0.1.8" && \
    pytest -q tests/test_browser.py --browser=chromium \
      --tracing=retain-on-failure \
      --screenshot=only-on-failure \
      --output=/test-results/browser'

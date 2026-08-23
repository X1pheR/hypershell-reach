#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
RESULTS_ROOT="${BROWSER_RESULTS_ROOT:-${ROOT_DIR}/test-results}"
RUN_ID="${BROWSER_RUN_ID:-$(date +%s)-$$}"
BROWSER_RESULTS_DIR="${RESULTS_ROOT}/browser/${RUN_ID}"
APP_LOG="${BROWSER_RESULTS_DIR}/hats-ui.log"
PLAYWRIGHT_IMAGE="mcr.microsoft.com/playwright/python:v1.62.0-noble"
BROWSER_LOCK_FILE="${BROWSER_LOCK_FILE:-${TMPDIR:-/tmp}/hats-ci-browser.lock}"
BROWSER_RUN_TIMEOUT_SECONDS="${BROWSER_RUN_TIMEOUT_SECONDS:-600}"
BROWSER_TEST_TIMEOUT_SECONDS="${BROWSER_TEST_TIMEOUT_SECONDS:-300}"
NETWORK="hats-browser-${RUN_ID}"
APP_CONTAINER="hats-browser-app-${RUN_ID}"
PLAYWRIGHT_CONTAINER="hats-browser-playwright-${RUN_ID}"
APP_IMAGE="hats-browser-app:${RUN_ID}"
BASE_URL="http://${APP_CONTAINER}:8080"
if [[ "${HATS_BROWSER_RUN_TIMEOUT_ACTIVE:-0}" != "1" ]]; then
  export HATS_BROWSER_RUN_TIMEOUT_ACTIVE=1
  exec timeout --signal=TERM --kill-after=30s "${BROWSER_RUN_TIMEOUT_SECONDS}" bash "$0" "$@"
fi

exec 9>"${BROWSER_LOCK_FILE}"
if ! flock -n 9; then
  echo "Another HATS browser acceptance run is already active" >&2
  exit 75
fi

FIXTURE_ROOT="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/hats-browser.XXXXXX")"
mkdir -p "${BROWSER_RESULTS_DIR}"
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

cat > "${FIXTURE_ROOT}/config.yaml" <<'YAML'
schema_version: 1
workspace:
  tmp: /fixture/tmp
  runs: /fixture/runs
  tasks: /fixture/tasks
  trash: /fixture/trash
sources:
  tools:
    - id: local
      path: /fixture/tools
  skills:
    - id: local
      path: /fixture/skills
  tooling_registry:
    path: /fixture/registry.md
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

app_created=0
playwright_created=0
network_created=0
image_created=0

cleanup() {
  set +e
  if [[ "${app_created}" -eq 1 ]]; then
    docker logs "${APP_CONTAINER}" > "${APP_LOG}" 2>&1 || true
  fi
  if [[ "${playwright_created}" -eq 1 ]]; then
    docker rm -f "${PLAYWRIGHT_CONTAINER}" >/dev/null 2>&1 || true
  fi
  if [[ "${app_created}" -eq 1 ]]; then
    docker rm -f "${APP_CONTAINER}" >/dev/null 2>&1 || true
  fi
  if [[ "${network_created}" -eq 1 ]]; then
    docker network rm "${NETWORK}" >/dev/null 2>&1 || true
  fi
  if [[ "${image_created}" -eq 1 ]]; then
    docker image rm -f "${APP_IMAGE}" >/dev/null 2>&1 || true
  fi
  rm -rf "${FIXTURE_ROOT}"
}
trap cleanup EXIT

docker build -t "${APP_IMAGE}" "${ROOT_DIR}"
image_created=1

docker network create "${NETWORK}" >/dev/null
network_created=1

docker create \
  --name "${APP_CONTAINER}" \
  --network "${NETWORK}" \
  -e HATS_CONFIG=/fixture/config.yaml \
  "${APP_IMAGE}" \
  hats-ui --config /fixture/config.yaml --host 0.0.0.0 --port 8080 >/dev/null
app_created=1

docker cp "${FIXTURE_ROOT}/." "${APP_CONTAINER}:/fixture"
docker start "${APP_CONTAINER}" >/dev/null

if ! docker run --rm --network "${NETWORK}" \
  -e TARGET_URL="${BASE_URL}/healthz" \
  "${PLAYWRIGHT_IMAGE}" \
  python -c 'import os, sys, time, urllib.request
url = os.environ["TARGET_URL"]
for _ in range(30):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status == 200:
                raise SystemExit(0)
    except Exception:
        pass
    time.sleep(1)
raise SystemExit(1)'; then
  echo "HATS UI did not become reachable from the browser-test network" >&2
  exit 1
fi

docker create \
  --name "${PLAYWRIGHT_CONTAINER}" \
  --init \
  --ipc=host \
  --network "${NETWORK}" \
  -e RUN_BROWSER_TESTS=1 \
  -e HATS_BROWSER_BASE_URL="${BASE_URL}" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTEST_ADDOPTS='-p no:cacheprovider' \
  -w /src \
  "${PLAYWRIGHT_IMAGE}" \
  bash -lc 'mkdir -p /src/tests /test-results/browser && \
    cp /test_browser.py /src/tests/test_browser.py && \
    python -m pip install --disable-pip-version-check -q \
      "playwright==1.62.0" \
      "pytest==9.1.1" \
      "pytest-asyncio==1.4.0" \
      "pytest-playwright==0.9.0" \
      "axe-playwright-python==0.1.8" && \
    pytest -q /src/tests/test_browser.py --browser=chromium \
      --tracing=retain-on-failure \
      --screenshot=only-on-failure \
      --output=/test-results/browser' >/dev/null
playwright_created=1

docker cp "${ROOT_DIR}/tests/test_browser.py" "${PLAYWRIGHT_CONTAINER}:/test_browser.py"

set +e
timeout --signal=TERM --kill-after=30s "${BROWSER_TEST_TIMEOUT_SECONDS}" \
  docker start -a "${PLAYWRIGHT_CONTAINER}"
test_status=$?
set -e

if [[ "${test_status}" -eq 124 ]]; then
  echo "HATS browser acceptance exceeded ${BROWSER_TEST_TIMEOUT_SECONDS}s" >&2
fi

docker cp "${PLAYWRIGHT_CONTAINER}:/test-results/browser/." "${BROWSER_RESULTS_DIR}/" 2>/dev/null || true
exit "${test_status}"

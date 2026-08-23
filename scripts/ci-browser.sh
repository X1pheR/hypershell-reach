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
  "${FIXTURE_ROOT}/trash" "${FIXTURE_ROOT}/candidates" "${FIXTURE_ROOT}/tools" "${FIXTURE_ROOT}/skills/example"

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

TASK_ID="task-20260820T120000000000Z-abcdef123456"
RUN_ID_FIXTURE="run-20260820T120100000000Z-123456abcdef"
mkdir -p "${FIXTURE_ROOT}/tasks/${TASK_ID}"
cat > "${FIXTURE_ROOT}/tasks/${TASK_ID}/task.yaml" <<YAML
schema_version: 2
revision: 1
id: ${TASK_ID}
title: Browser WP6 continuity fixture
objective: Render safe Task continuity in browser acceptance.
project_ref: projects/browser-fixture.md
status: active
next_action: Verify exact read-only provenance links.
continuity:
  authorization: Browser acceptance only.
  sources:
    - classification: configured
      reference: fixture/config.yaml
      purpose: Browser fixture source.
  completed:
    - Created safe browser fixture.
  validation:
    - Detail navigation pending.
  cleanup: []
  recovery: Remove browser fixture directory.
  blockers: []
  assumptions: []
retained: false
created_at: '2026-08-20T12:00:00.000000Z'
updated_at: '2026-08-20T12:00:00.000000Z'
archived_at: null
YAML

cat > "${FIXTURE_ROOT}/runs/${RUN_ID_FIXTURE}.json" <<JSON
{
  "schema_version": 2,
  "id": "${RUN_ID_FIXTURE}",
  "operation": "run_script",
  "target": "docker",
  "purpose": "Verify exact read-only browser provenance navigation.",
  "result_summary": "Execution succeeded with exit_code=0. Output content was not persisted; observed stdout_bytes=0, stderr_bytes=0, stdout_truncated=false, stderr_truncated=false.",
  "task_id": "${TASK_ID}",
  "script_id": "system.inspect",
  "script_source": "local",
  "script_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "argument_names": [],
  "timeout_seconds": 30,
  "may_mutate": false,
  "idempotent": true,
  "retained": false,
  "started_at": "2026-08-20T12:01:00.000000Z",
  "ended_at": "2026-08-20T12:01:00.010000Z",
  "status": "succeeded",
  "ambiguous": false,
  "exit_code": 0,
  "timed_out": false,
  "duration_ms": 10,
  "stdout_bytes": 0,
  "stderr_bytes": 0,
  "stdout_truncated": false,
  "stderr_truncated": false,
  "error_type": null
}
JSON

cat > "${FIXTURE_ROOT}/candidates/ATR-999.yaml" <<YAML
schema_version: 1
id: ATR-999
revision: 4
title: Browser WP6 structured candidate
problem:
  summary: Repeated product review needs exact provenance navigation.
  cause: List-only views hide decision context.
  recurrence: The relationship is reviewed across Tasks, Runs and Candidates.
  evidence:
    - WP6 requires exact read-only deep links.
proposal:
  capability: Render structured provenance detail without mutation controls.
  proposed_tool_id: system.inspect
  required_inputs:
    - name: record_id
      description: Stable record identifier.
  expected_outputs:
    - name: detail
      description: Safe read-only detail view.
  safety:
    mutating: false
    secret_access: false
    boundary: Never render raw command, argument values or output content.
  acceptance:
    - Candidate links to the exact implementation Task and final managed Tool.
ownership:
  owner_id: X1pheR/homelab-agent-tooling-skills-mcp
promotion:
  state: implemented
  rationale: Read-only provenance detail is reusable product behavior.
  state_reason: Browser fixture implementation accepted.
implementation:
  task_id: ${TASK_ID}
  final_reference:
    kind: managed-tool
    id: system.inspect
created_at: '2026-08-20T12:02:00.000000Z'
updated_at: '2026-08-20T12:03:00.000000Z'
YAML

cat > "${FIXTURE_ROOT}/config.yaml" <<'YAML'
schema_version: 1
workspace:
  tmp: /fixture/tmp
  runs: /fixture/runs
  tasks: /fixture/tasks
  trash: /fixture/trash
  candidates: /fixture/candidates
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

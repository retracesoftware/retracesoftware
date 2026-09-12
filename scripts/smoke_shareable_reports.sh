#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
REPORT_JSON="${REPORT_JSON:-${ROOT_DIR}/tests/fixtures/dataframe_ai_report.json}"
REPO_ROOT_FOR_REDACTION="${REPO_ROOT_FOR_REDACTION:-/workspace/dataframe-test-example}"
REPORT_HOST="${REPORT_HOST:-127.0.0.1}"
REPORT_PORT="${REPORT_PORT:-8877}"
REPORT_API_KEY="${REPORT_API_KEY:-smoke-key}"
WORK_DIR="${WORK_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/retrace-share-smoke.XXXXXX")}"
STORAGE_ROOT="${STORAGE_ROOT:-${WORK_DIR}/storage}"
BASE_URL="http://${REPORT_HOST}:${REPORT_PORT}"
ENDPOINT="${BASE_URL}/api/reports"
SERVER_LOG="${WORK_DIR}/report-server.log"
SERVER_PID=""

cleanup() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

run_retrace() {
  PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" \
    "${PYTHON_BIN}" -m retracesoftware.cli "$@"
}

wait_for_server() {
  for _ in $(seq 1 50); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "${BASE_URL}/r/not-a-report" || true)"
    if [[ "${code}" == "404" ]]; then
      return 0
    fi
    sleep 0.1
  done
  echo "report server did not become ready" >&2
  cat "${SERVER_LOG}" >&2 || true
  return 1
}

first_url_from() {
  awk '/^https?:\/\// { print; exit }' "$1"
}

delete_token_from() {
  awk '/^Delete token:/ { getline; print; exit }' "$1"
}

assert_contains() {
  local needle="$1"
  local file="$2"
  if ! grep -Fq "$needle" "$file"; then
    echo "expected ${file} to contain: ${needle}" >&2
    return 1
  fi
}

assert_not_contains() {
  local needle="$1"
  local file="$2"
  if grep -Fq "$needle" "$file"; then
    echo "expected ${file} not to contain: ${needle}" >&2
    return 1
  fi
}

mkdir -p "${WORK_DIR}" "${STORAGE_ROOT}"

echo "Starting local report service on ${BASE_URL}"
run_retrace report-server \
  --api-key "${REPORT_API_KEY}" \
  --host "${REPORT_HOST}" \
  --port "${REPORT_PORT}" \
  --storage-root "${STORAGE_ROOT}" \
  >"${SERVER_LOG}" 2>&1 &
SERVER_PID="$!"
wait_for_server

echo "Uploading public report from ${REPORT_JSON}"
PUBLIC_OUT="${WORK_DIR}/public-upload.out"
export RETRACE_API_KEY="${REPORT_API_KEY}"
run_retrace report \
  --json "${REPORT_JSON}" \
  --repo-root "${REPO_ROOT_FOR_REDACTION}" \
  --share \
  --endpoint "${ENDPOINT}" \
  --public-json "${WORK_DIR}/public-preview.json" \
  --public-html "${WORK_DIR}/public-preview.html" \
  --public-markdown "${WORK_DIR}/public-preview.md" \
  >"${PUBLIC_OUT}" 2>&1

PUBLIC_URL="$(first_url_from "${PUBLIC_OUT}")"
PUBLIC_DELETE_TOKEN="$(delete_token_from "${PUBLIC_OUT}")"
if [[ -z "${PUBLIC_URL}" || -z "${PUBLIC_DELETE_TOKEN}" ]]; then
  echo "public upload did not print URL and delete token" >&2
  cat "${PUBLIC_OUT}" >&2
  exit 1
fi

curl -fsS "${PUBLIC_URL}" -o "${WORK_DIR}/public-served.html"
curl -fsS "${PUBLIC_URL}.json" -o "${WORK_DIR}/public-served.json"
assert_contains "DataFrame amount_gbp mismatch" "${WORK_DIR}/public-served.html"
assert_contains "Copy GitHub comment" "${WORK_DIR}/public-served.html"
assert_contains "DAP stack trace has 43 frame(s)" "${WORK_DIR}/public-served.html"
assert_contains "\"tool_transcript_included\": false" "${WORK_DIR}/public-served.json"
assert_not_contains "/workspace/dataframe-test-example" "${WORK_DIR}/public-served.json"

echo "Uploading full diagnostic report"
FULL_OUT="${WORK_DIR}/full-upload.out"
run_retrace report \
  --json "${REPORT_JSON}" \
  --share-full \
  --yes-share-full \
  --endpoint "${ENDPOINT}" \
  >"${FULL_OUT}" 2>&1

FULL_URL="$(first_url_from "${FULL_OUT}")"
FULL_DELETE_TOKEN="$(delete_token_from "${FULL_OUT}")"
if [[ -z "${FULL_URL}" || -z "${FULL_DELETE_TOKEN}" ]]; then
  echo "full upload did not print URL and delete token" >&2
  cat "${FULL_OUT}" >&2
  exit 1
fi

curl -fsS "${FULL_URL}" -o "${WORK_DIR}/full-served.html"
curl -fsS "${FULL_URL}.json" -o "${WORK_DIR}/full-served.json"
assert_contains "Full diagnostic report" "${WORK_DIR}/full-served.html"
assert_contains "start_replay_session" "${WORK_DIR}/full-served.html"
assert_contains "\"tool_transcript_included\": true" "${WORK_DIR}/full-served.json"
assert_contains "/workspace/dataframe-test-example/tests/test_financial_report.py" "${WORK_DIR}/full-served.json"

echo "Deleting public report"
curl -fsS -X DELETE \
  -H "Authorization: Bearer ${PUBLIC_DELETE_TOKEN}" \
  "${ENDPOINT}/${PUBLIC_URL##*/}" \
  -o "${WORK_DIR}/public-delete.json"
assert_contains "\"deleted\": true" "${WORK_DIR}/public-delete.json"

deleted_code="$(curl -sS -o "${WORK_DIR}/public-after-delete.json" -w '%{http_code}' "${PUBLIC_URL}.json")"
if [[ "${deleted_code}" != "404" ]]; then
  echo "expected deleted public report to return 404, got ${deleted_code}" >&2
  exit 1
fi

echo "Deleting full report"
curl -fsS -X DELETE \
  -H "Authorization: Bearer ${FULL_DELETE_TOKEN}" \
  "${ENDPOINT}/${FULL_URL##*/}" \
  -o "${WORK_DIR}/full-delete.json"
assert_contains "\"deleted\": true" "${WORK_DIR}/full-delete.json"

echo "Shareable reports smoke passed"
echo "Work dir: ${WORK_DIR}"

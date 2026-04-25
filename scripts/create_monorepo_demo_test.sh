#!/usr/bin/env bash
# Integration test for scripts/create-monorepo-demo.sh.
#
# Runs the script in a sandboxed temp directory with an isolated HOME
# (so the test does not depend on the host's git identity and never
# mutates it), then asserts the resulting demo has the expected
# layout, .weld/discover.yaml, and a seeded git history at the root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT_DIR}/create-monorepo-demo.sh"

ERRORS=0
fail() { printf 'FAIL: %s\n' "$1" >&2; ERRORS=$((ERRORS + 1)); }
pass() { printf 'PASS: %s\n' "$1"; }

assert_file() {
  if [ -f "$1" ]; then pass "file exists: $1"; else fail "missing file: $1"; fi
}
assert_dir() {
  if [ -d "$1" ]; then pass "dir exists: $1"; else fail "missing dir: $1"; fi
}

SANDBOX="$(mktemp -d)"
trap 'rm -rf "${SANDBOX}"' EXIT
export HOME="${SANDBOX}/home"
mkdir -p "${HOME}"
git config --global user.name  "Demo Tester"
git config --global user.email "demo@example.com"
git config --global init.defaultBranch main

TARGET="${SANDBOX}/mono"

if ! "${SCRIPT}" "${TARGET}" >"${SANDBOX}/stdout" 2>"${SANDBOX}/stderr"; then
  fail "script exited non-zero. stderr:"
  cat "${SANDBOX}/stderr" >&2
fi
if grep -q "Monorepo demo ready" "${SANDBOX}/stdout"; then
  pass "script reported readiness"
else
  fail "script did not print readiness banner"
fi

assert_dir  "${TARGET}"
assert_dir  "${TARGET}/.weld"
assert_dir  "${TARGET}/.git"
assert_file "${TARGET}/.weld/discover.yaml"
assert_file "${TARGET}/README.md"
assert_file "${TARGET}/package.json"

assert_file "${TARGET}/packages/ui/src/Button.tsx"
assert_file "${TARGET}/packages/ui/package.json"
assert_file "${TARGET}/packages/api/src/client.ts"
assert_file "${TARGET}/apps/web/src/App.tsx"
assert_file "${TARGET}/libs/shared-types/src/index.ts"
assert_file "${TARGET}/services/orders-api/src/server.ts"

if git -C "${TARGET}" rev-parse --verify HEAD >/dev/null 2>&1; then
  pass "monorepo has seed commit"
else
  fail "monorepo has no commits"
fi

# Discover config references the expected packages.
DC="${TARGET}/.weld/discover.yaml"
for pkg in "pkg:ui" "pkg:api" "pkg:web" "lib:shared-types" "service:orders-api"; do
  if grep -q "${pkg}" "${DC}"; then
    pass "discover.yaml references package ${pkg}"
  else
    fail "discover.yaml missing package ${pkg}"
  fi
done

# Re-running on the same target must fail fast.
if "${SCRIPT}" "${TARGET}" >/dev/null 2>"${SANDBOX}/rerun.stderr"; then
  fail "script unexpectedly succeeded against a non-empty target"
else
  if grep -q "not empty" "${SANDBOX}/rerun.stderr"; then
    pass "script refuses to clobber non-empty target"
  else
    fail "script failed but for the wrong reason"
    cat "${SANDBOX}/rerun.stderr" >&2
  fi
fi

if [ "${ERRORS}" -ne 0 ]; then
  printf '\n%d test(s) failed.\n' "${ERRORS}" >&2
  exit 1
fi
printf '\nAll monorepo demo tests passed.\n'

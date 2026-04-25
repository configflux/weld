#!/usr/bin/env bash
# Integration test for scripts/create-polyrepo-demo.sh.
#
# Runs the script in a sandboxed temp directory with an isolated HOME
# (so the test does not depend on the host's git identity and never
# mutates it), then asserts the resulting demo has the expected layout,
# .weld configs, and seeded git history in every child.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT_DIR}/create-polyrepo-demo.sh"

ERRORS=0
fail() { printf 'FAIL: %s\n' "$1" >&2; ERRORS=$((ERRORS + 1)); }
pass() { printf 'PASS: %s\n' "$1"; }

assert_file() {
  if [ -f "$1" ]; then pass "file exists: $1"; else fail "missing file: $1"; fi
}
assert_dir() {
  if [ -d "$1" ]; then pass "dir exists: $1"; else fail "missing dir: $1"; fi
}

# Sandbox: isolated HOME so `git config --global` only sees what we
# put in this test, and a clean target directory.
SANDBOX="$(mktemp -d)"
trap 'rm -rf "${SANDBOX}"' EXIT
export HOME="${SANDBOX}/home"
mkdir -p "${HOME}"

# Configure a global identity inside the sandbox so the script's
# success path runs without touching the host config.
git config --global user.name  "Demo Tester"
git config --global user.email "demo@example.com"
git config --global init.defaultBranch main

TARGET="${SANDBOX}/poly"

# ---------------------------------------------------------------------------
# Test 1: Script exits 0 and prints expected output.
# ---------------------------------------------------------------------------
if ! "${SCRIPT}" "${TARGET}" >"${SANDBOX}/stdout" 2>"${SANDBOX}/stderr"; then
  fail "script exited non-zero. stderr:"
  cat "${SANDBOX}/stderr" >&2
fi
if grep -q "Polyrepo demo ready" "${SANDBOX}/stdout"; then
  pass "script reported readiness"
else
  fail "script did not print readiness banner"
fi

# ---------------------------------------------------------------------------
# Test 2: Required directories and config files exist.
# ---------------------------------------------------------------------------
assert_dir "${TARGET}"
assert_dir "${TARGET}/.weld"
assert_file "${TARGET}/.weld/workspaces.yaml"
assert_file "${TARGET}/.weld/discover.yaml"
assert_file "${TARGET}/README.md"

for child in services/api services/auth libs/shared-models; do
  assert_dir  "${TARGET}/${child}"
  assert_dir  "${TARGET}/${child}/.weld"
  assert_file "${TARGET}/${child}/.weld/discover.yaml"
  assert_dir  "${TARGET}/${child}/.git"
done

# ---------------------------------------------------------------------------
# Test 3: Seed source files exist in each child.
# ---------------------------------------------------------------------------
assert_file "${TARGET}/services/api/src/server.py"
assert_file "${TARGET}/services/auth/src/app.py"
assert_file "${TARGET}/services/auth/src/routers/tokens.py"
assert_file "${TARGET}/libs/shared-models/src/shared_models/models.py"

# ---------------------------------------------------------------------------
# Test 4: Each child has at least one commit.
# ---------------------------------------------------------------------------
for child in services/api services/auth libs/shared-models; do
  if git -C "${TARGET}/${child}" rev-parse --verify HEAD >/dev/null 2>&1; then
    pass "child has seed commit: ${child}"
  else
    fail "child has no commits: ${child}"
  fi
done

# ---------------------------------------------------------------------------
# Test 5: workspaces.yaml lists all three children with the expected
# cross_repo_strategies entry.
# ---------------------------------------------------------------------------
WS="${TARGET}/.weld/workspaces.yaml"
for name in services-api services-auth libs-shared-models; do
  if grep -q "name: ${name}" "${WS}"; then
    pass "workspaces.yaml lists ${name}"
  else
    fail "workspaces.yaml missing child: ${name}"
  fi
done
if grep -q "service_graph" "${WS}"; then
  pass "workspaces.yaml declares service_graph resolver"
else
  fail "workspaces.yaml missing service_graph resolver"
fi

# ---------------------------------------------------------------------------
# Test 6: Re-running on a non-empty target fails fast (does not clobber).
# ---------------------------------------------------------------------------
if "${SCRIPT}" "${TARGET}" >/dev/null 2>"${SANDBOX}/rerun.stderr"; then
  fail "script unexpectedly succeeded against a non-empty target"
else
  if grep -q "not empty" "${SANDBOX}/rerun.stderr"; then
    pass "script refuses to clobber non-empty target"
  else
    fail "script failed but for the wrong reason; stderr:"
    cat "${SANDBOX}/rerun.stderr" >&2
  fi
fi

# ---------------------------------------------------------------------------
# Test 7: Missing argument fails gracefully.
# ---------------------------------------------------------------------------
if "${SCRIPT}" >/dev/null 2>"${SANDBOX}/noarg.stderr"; then
  fail "script unexpectedly succeeded with no target argument"
else
  if grep -q "missing target" "${SANDBOX}/noarg.stderr"; then
    pass "script reports missing target argument"
  else
    fail "script failed but did not name the missing-target reason"
    cat "${SANDBOX}/noarg.stderr" >&2
  fi
fi

if [ "${ERRORS}" -ne 0 ]; then
  printf '\n%d test(s) failed.\n' "${ERRORS}" >&2
  exit 1
fi
printf '\nAll polyrepo demo tests passed.\n'

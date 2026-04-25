#!/usr/bin/env bash
# Tests that the demo bootstrap scripts fail gracefully when no git
# identity is configured (system + global), with a non-zero exit and
# an actionable error message.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLY="${SCRIPT_DIR}/create-polyrepo-demo.sh"
MONO="${SCRIPT_DIR}/create-monorepo-demo.sh"

ERRORS=0
fail() { printf 'FAIL: %s\n' "$1" >&2; ERRORS=$((ERRORS + 1)); }
pass() { printf 'PASS: %s\n' "$1"; }

SANDBOX="$(mktemp -d)"
trap 'rm -rf "${SANDBOX}"' EXIT

# Sandbox HOME with no global git identity. Combined with the script's
# decision to query system+global only (and ignore any enclosing
# repo-local identity), this guarantees `git config` reports no
# identity to the script.
export HOME="${SANDBOX}/home"
mkdir -p "${HOME}"

# Run from a directory with no enclosing repo to make extra sure no
# inherited local config is in scope.
TARGET="${SANDBOX}/poly"
(
  cd "${SANDBOX}"
  "${POLY}" "${TARGET}" >"${SANDBOX}/poly.out" 2>"${SANDBOX}/poly.err"
) && rc=0 || rc=$?

if [ "$rc" -eq 0 ]; then
  fail "polyrepo script unexpectedly succeeded without a git identity"
else
  pass "polyrepo script exits non-zero without a git identity (rc=$rc)"
fi
if grep -q "git identity is not configured" "${SANDBOX}/poly.err"; then
  pass "polyrepo script prints the expected guidance"
else
  fail "polyrepo error did not mention git identity"
  cat "${SANDBOX}/poly.err" >&2
fi
if [ -d "${TARGET}/services" ] && [ -d "${TARGET}/services/api/.git" ]; then
  fail "polyrepo script left partial git state behind: services/api/.git"
fi

# Same coverage for the monorepo script.
TARGET2="${SANDBOX}/mono"
(
  cd "${SANDBOX}"
  "${MONO}" "${TARGET2}" >"${SANDBOX}/mono.out" 2>"${SANDBOX}/mono.err"
) && rc2=0 || rc2=$?

if [ "$rc2" -eq 0 ]; then
  fail "monorepo script unexpectedly succeeded without a git identity"
else
  pass "monorepo script exits non-zero without a git identity (rc=$rc2)"
fi
if grep -q "git identity is not configured" "${SANDBOX}/mono.err"; then
  pass "monorepo script prints the expected guidance"
else
  fail "monorepo error did not mention git identity"
  cat "${SANDBOX}/mono.err" >&2
fi

if [ "${ERRORS}" -ne 0 ]; then
  printf '\n%d test(s) failed.\n' "${ERRORS}" >&2
  exit 1
fi
printf '\nAll git-identity-failure tests passed.\n'

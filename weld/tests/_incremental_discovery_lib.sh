# shellcheck shell=bash
# _incremental_discovery_lib.sh: shared scaffolding for the incremental
# discovery sh_test suite (ADR 0008 content-hash state tracking).
#
# The 10 original scenarios from weld_incremental_discovery_test.sh are split
# across three focused sh_test scripts that each source this helper:
#   - weld_incremental_discovery_state_test.sh     (state file + no-change idempotency)
#   - weld_incremental_discovery_mutations_test.sh (modify / add / delete)
#   - weld_incremental_discovery_fallback_test.sh  (missing/corrupt state, flags, version mismatch)
#
# This file is sourced, not executed. It expects the sourcing script to have
# already sourced weld_test_lib.sh and to define the globals ROOT, TMPDIR,
# PASS_COUNT, and FAIL_COUNT before calling these helpers.

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  echo "  PASS: $1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo "  FAIL: $1"
}

# ---------------------------------------------------------------------------
# Setup: create a minimal project with discover.yaml and source files
# ---------------------------------------------------------------------------

setup_project() {
  local project_dir="$1"
  rm -rf "${project_dir}"
  mkdir -p "${project_dir}/.weld"
  mkdir -p "${project_dir}/src"

  # Initialize as git repo (needed for git_sha in meta)
  (cd "${project_dir}" && git init -q && git config user.email "test@test.com" && git config user.name "test")

  # Write discover.yaml
  cat > "${project_dir}/.weld/discover.yaml" <<'YAML'
sources:
  - glob: "src/*.py"
    type: file
    strategy: python_module
  - files: ["README.md"]
    type: config
    strategy: config_file
YAML

  # Write source files
  cat > "${project_dir}/src/alpha.py" <<'PY'
"""Alpha module."""

class AlphaService:
    pass

def alpha_handler():
    pass
PY

  cat > "${project_dir}/src/beta.py" <<'PY'
"""Beta module."""

class BetaModel:
    pass
PY

  cat > "${project_dir}/README.md" <<'MD'
# Test Project
MD

  (cd "${project_dir}" && git add -A && git commit -q -m "init")
}

# ---------------------------------------------------------------------------
# Helper: run wd discover with given flags
# ---------------------------------------------------------------------------

run_discover() {
  local project_dir="$1"
  shift
  weld_in_root "${ROOT}" "${project_dir}" discover "$@" "${project_dir}"
}

# ---------------------------------------------------------------------------
# Summary: print pass/fail tally and exit non-zero if any test failed
# ---------------------------------------------------------------------------

report_results() {
  local suite="$1"
  echo ""
  echo "=== ${suite} Results ==="
  echo "  Passed: ${PASS_COUNT}"
  echo "  Failed: ${FAIL_COUNT}"
  echo ""

  if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    echo "FAIL: ${FAIL_COUNT} test(s) failed"
    exit 1
  fi

  echo "PASS: all ${PASS_COUNT} tests passed"
}

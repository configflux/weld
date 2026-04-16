#!/usr/bin/env bash
set -euo pipefail

# weld_incremental_discovery_test: validates incremental discovery with
# content-hash state tracking per ADR 0008.
#
# Test scenarios:
#   1. Full discovery produces state file alongside graph
#   2. Incremental discovery with no changes returns same graph
#   3. File modification triggers re-extraction of changed file only
#   4. File addition adds new nodes without losing existing ones
#   5. File deletion removes nodes sourced from deleted file
#   6. Fallback to full when state file is missing
#   7. Fallback to full when state file is corrupt
#   8. --full flag forces full discovery even with valid state
#   9. --incremental flag with no state falls back to full
#  10. State file version mismatch triggers full fallback

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${SCRIPT_DIR}/weld_test_lib.sh"
ROOT="$(weld_test_repo_root "${SCRIPT_DIR}")"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

PASS_COUNT=0
FAIL_COUNT=0

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
# Test 1: Full discovery produces state file alongside graph
# ---------------------------------------------------------------------------

echo "--- Test 1: Full discovery produces state file ---"
PROJECT="${TMPDIR}/test1"
setup_project "${PROJECT}"

GRAPH_OUT="${TMPDIR}/graph1.json"
run_discover "${PROJECT}" --full > "${GRAPH_OUT}"

STATE_FILE="${PROJECT}/.weld/discovery-state.json"
if [[ -f "${STATE_FILE}" ]]; then
  pass "state file created"
else
  fail "state file not created"
fi

# Validate state file structure
python3 -c "
import json, sys
with open('${STATE_FILE}') as f:
    state = json.load(f)
errors = []
if state.get('version') != 1:
    errors.append(f'expected version 1, got {state.get(\"version\")}')
if 'created_at' not in state:
    errors.append('missing created_at')
if 'files' not in state:
    errors.append('missing files')
elif not isinstance(state['files'], dict):
    errors.append('files is not a dict')
else:
    # Should have hashes for source files
    files = state['files']
    if not any('alpha' in k for k in files):
        errors.append('alpha.py not in state files')
    if not any('beta' in k for k in files):
        errors.append('beta.py not in state files')
    # All hashes should start with sha256:
    for k, v in files.items():
        if not v.startswith('sha256:'):
            errors.append(f'{k} hash does not start with sha256: got {v}')
if errors:
    print('FAIL: ' + '; '.join(errors))
    sys.exit(1)
" && pass "state file has correct structure" || fail "state file structure invalid"

# Validate graph has nodes
python3 -c "
import json, sys
with open('${GRAPH_OUT}') as f:
    graph = json.load(f)
nodes = graph.get('nodes', {})
if len(nodes) < 2:
    print(f'FAIL: expected >=2 nodes, got {len(nodes)}')
    sys.exit(1)
" && pass "full discovery produced graph with nodes" || fail "full discovery graph invalid"


# ---------------------------------------------------------------------------
# Test 2: Incremental with no changes returns same graph
# ---------------------------------------------------------------------------

echo "--- Test 2: Incremental with no changes ---"
PROJECT="${TMPDIR}/test2"
setup_project "${PROJECT}"

# First: full discovery (creates state file)
GRAPH_FULL="${TMPDIR}/graph2_full.json"
run_discover "${PROJECT}" --full > "${GRAPH_FULL}"

# Save graph for incremental to read
cp "${GRAPH_FULL}" "${PROJECT}/.weld/graph.json"

# Second: incremental (no changes)
GRAPH_INCR="${TMPDIR}/graph2_incr.json"
run_discover "${PROJECT}" --incremental > "${GRAPH_INCR}"

python3 -c "
import json, sys

with open('${GRAPH_FULL}') as f:
    full = json.load(f)
with open('${GRAPH_INCR}') as f:
    incr = json.load(f)

# Nodes should be identical
full_ids = sorted(full['nodes'].keys())
incr_ids = sorted(incr['nodes'].keys())
if full_ids != incr_ids:
    print(f'FAIL: node IDs differ. full={full_ids}, incr={incr_ids}')
    sys.exit(1)

# Edge count should match
if len(full['edges']) != len(incr['edges']):
    print(f'FAIL: edge count differs. full={len(full[\"edges\"])}, incr={len(incr[\"edges\"])}')
    sys.exit(1)
" && pass "incremental with no changes preserves graph" || fail "incremental with no changes changed graph"


# ---------------------------------------------------------------------------
# Test 3: File modification triggers re-extraction
# ---------------------------------------------------------------------------

echo "--- Test 3: File modification ---"
PROJECT="${TMPDIR}/test3"
setup_project "${PROJECT}"

# Full discovery first
GRAPH_BEFORE="${TMPDIR}/graph3_before.json"
run_discover "${PROJECT}" --full > "${GRAPH_BEFORE}"

# Save graph for incremental to read
cp "${GRAPH_BEFORE}" "${PROJECT}/.weld/graph.json"

# Modify alpha.py: add a new class
cat > "${PROJECT}/src/alpha.py" <<'PY'
"""Alpha module -- modified."""

class AlphaService:
    pass

class AlphaHelper:
    """New class added."""
    pass

def alpha_handler():
    pass
PY

(cd "${PROJECT}" && git add -A && git commit -q -m "modify alpha")

# Run incremental
GRAPH_AFTER="${TMPDIR}/graph3_after.json"
run_discover "${PROJECT}" --incremental > "${GRAPH_AFTER}"

python3 -c "
import json, sys

with open('${GRAPH_BEFORE}') as f:
    before = json.load(f)
with open('${GRAPH_AFTER}') as f:
    after = json.load(f)

# Alpha node should still exist
alpha_nodes_before = {k: v for k, v in before['nodes'].items()
                     if v.get('props', {}).get('file', '').endswith('alpha.py')}
alpha_nodes_after = {k: v for k, v in after['nodes'].items()
                    if v.get('props', {}).get('file', '').endswith('alpha.py')}

if not alpha_nodes_after:
    print('FAIL: alpha node missing after modification')
    sys.exit(1)

# The modified alpha should now export AlphaHelper
for nid, node in alpha_nodes_after.items():
    exports = node.get('props', {}).get('exports', [])
    if 'AlphaHelper' in exports:
        break
else:
    print(f'FAIL: AlphaHelper not in exports after modification. Nodes: {alpha_nodes_after}')
    sys.exit(1)

# Beta node should still exist (unchanged)
beta_nodes = {k: v for k, v in after['nodes'].items()
              if v.get('props', {}).get('file', '').endswith('beta.py')}
if not beta_nodes:
    print('FAIL: beta node lost during incremental')
    sys.exit(1)
" && pass "modification detected and re-extracted" || fail "modification not handled correctly"


# ---------------------------------------------------------------------------
# Test 4: File addition adds new nodes
# ---------------------------------------------------------------------------

echo "--- Test 4: File addition ---"
PROJECT="${TMPDIR}/test4"
setup_project "${PROJECT}"

# Full discovery
GRAPH_BEFORE="${TMPDIR}/graph4_before.json"
run_discover "${PROJECT}" --full > "${GRAPH_BEFORE}"
cp "${GRAPH_BEFORE}" "${PROJECT}/.weld/graph.json"

# Add a new file
cat > "${PROJECT}/src/gamma.py" <<'PY'
"""Gamma module."""

class GammaProcessor:
    pass
PY

(cd "${PROJECT}" && git add -A && git commit -q -m "add gamma")

# Run incremental
GRAPH_AFTER="${TMPDIR}/graph4_after.json"
run_discover "${PROJECT}" --incremental > "${GRAPH_AFTER}"

python3 -c "
import json, sys

with open('${GRAPH_BEFORE}') as f:
    before = json.load(f)
with open('${GRAPH_AFTER}') as f:
    after = json.load(f)

before_ids = set(before['nodes'].keys())
after_ids = set(after['nodes'].keys())

# New node(s) should appear for gamma
gamma_nodes = {k for k in after_ids if 'gamma' in k.lower()}
if not gamma_nodes:
    # Check by props.file
    gamma_by_file = {k for k, v in after['nodes'].items()
                     if 'gamma' in v.get('props', {}).get('file', '')}
    if not gamma_by_file:
        print(f'FAIL: no gamma node found after addition. After IDs: {sorted(after_ids)}')
        sys.exit(1)

# Existing nodes should still be present
if not before_ids.issubset(after_ids):
    lost = before_ids - after_ids
    print(f'FAIL: lost nodes during file addition: {lost}')
    sys.exit(1)
" && pass "file addition adds new nodes without losing existing" || fail "file addition failed"


# ---------------------------------------------------------------------------
# Test 5: File deletion removes nodes
# ---------------------------------------------------------------------------

echo "--- Test 5: File deletion ---"
PROJECT="${TMPDIR}/test5"
setup_project "${PROJECT}"

# Full discovery
GRAPH_BEFORE="${TMPDIR}/graph5_before.json"
run_discover "${PROJECT}" --full > "${GRAPH_BEFORE}"
cp "${GRAPH_BEFORE}" "${PROJECT}/.weld/graph.json"

# Verify beta exists before deletion
python3 -c "
import json, sys
with open('${GRAPH_BEFORE}') as f:
    g = json.load(f)
beta = {k: v for k, v in g['nodes'].items()
        if v.get('props', {}).get('file', '').endswith('beta.py')}
if not beta:
    print('FAIL: beta node not found before deletion')
    sys.exit(1)
" || { fail "beta not in graph before deletion"; }

# Delete beta.py
rm "${PROJECT}/src/beta.py"
(cd "${PROJECT}" && git add -A && git commit -q -m "delete beta")

# Run incremental
GRAPH_AFTER="${TMPDIR}/graph5_after.json"
run_discover "${PROJECT}" --incremental > "${GRAPH_AFTER}"

python3 -c "
import json, sys

with open('${GRAPH_AFTER}') as f:
    after = json.load(f)

# Beta nodes should be gone
beta_nodes = {k: v for k, v in after['nodes'].items()
              if v.get('props', {}).get('file', '').endswith('beta.py')}
if beta_nodes:
    print(f'FAIL: beta node still present after deletion: {list(beta_nodes.keys())}')
    sys.exit(1)

# Alpha should still exist
alpha_nodes = {k: v for k, v in after['nodes'].items()
               if v.get('props', {}).get('file', '').endswith('alpha.py')}
if not alpha_nodes:
    print('FAIL: alpha node lost during deletion of beta')
    sys.exit(1)

# No edges should reference deleted nodes
node_ids = set(after['nodes'].keys())
for e in after['edges']:
    if e['from'] not in node_ids:
        print(f'FAIL: dangling edge from={e[\"from\"]}')
        sys.exit(1)
    if e['to'] not in node_ids:
        print(f'FAIL: dangling edge to={e[\"to\"]}')
        sys.exit(1)
" && pass "file deletion removes nodes and cleans edges" || fail "file deletion not handled correctly"


# ---------------------------------------------------------------------------
# Test 6: Fallback to full when state file is missing
# ---------------------------------------------------------------------------

echo "--- Test 6: Fallback on missing state ---"
PROJECT="${TMPDIR}/test6"
setup_project "${PROJECT}"

# Run discover without --full (no state file exists -> should auto-full)
GRAPH_OUT="${TMPDIR}/graph6.json"
run_discover "${PROJECT}" > "${GRAPH_OUT}"

STATE_FILE="${PROJECT}/.weld/discovery-state.json"
if [[ -f "${STATE_FILE}" ]]; then
  pass "auto-full creates state file when none exists"
else
  fail "auto-full did not create state file"
fi

python3 -c "
import json, sys
with open('${GRAPH_OUT}') as f:
    g = json.load(f)
if len(g.get('nodes', {})) < 2:
    print(f'FAIL: expected >=2 nodes, got {len(g.get(\"nodes\", {}))}')
    sys.exit(1)
" && pass "fallback full discovery produces valid graph" || fail "fallback graph invalid"


# ---------------------------------------------------------------------------
# Test 7: Fallback on corrupt state file
# ---------------------------------------------------------------------------

echo "--- Test 7: Fallback on corrupt state ---"
PROJECT="${TMPDIR}/test7"
setup_project "${PROJECT}"

# Create a corrupt state file
mkdir -p "${PROJECT}/.weld"
echo "NOT VALID JSON {{{" > "${PROJECT}/.weld/discovery-state.json"

# Run discover (should fall back to full)
GRAPH_OUT="${TMPDIR}/graph7.json"
run_discover "${PROJECT}" > "${GRAPH_OUT}" 2>/dev/null

python3 -c "
import json, sys
with open('${GRAPH_OUT}') as f:
    g = json.load(f)
if len(g.get('nodes', {})) < 2:
    print(f'FAIL: expected >=2 nodes after corrupt fallback, got {len(g.get(\"nodes\", {}))}')
    sys.exit(1)
" && pass "corrupt state triggers full fallback" || fail "corrupt state fallback failed"

# Verify state was re-created correctly
python3 -c "
import json, sys
with open('${PROJECT}/.weld/discovery-state.json') as f:
    state = json.load(f)
if state.get('version') != 1:
    print(f'FAIL: state version incorrect after recovery: {state.get(\"version\")}')
    sys.exit(1)
" && pass "state file recovered after corruption" || fail "state file not recovered"


# ---------------------------------------------------------------------------
# Test 8: --full flag forces full discovery
# ---------------------------------------------------------------------------

echo "--- Test 8: --full forces full discovery ---"
PROJECT="${TMPDIR}/test8"
setup_project "${PROJECT}"

# First: full discovery to create state
run_discover "${PROJECT}" --full > /dev/null

# Modify a file
cat > "${PROJECT}/src/alpha.py" <<'PY'
"""Alpha -- forced full."""

class AlphaForced:
    pass
PY
(cd "${PROJECT}" && git add -A && git commit -q -m "modify for forced full")

# Save graph
run_discover "${PROJECT}" --full > "${PROJECT}/.weld/graph.json"

# Run --full explicitly (should re-extract everything)
GRAPH_OUT="${TMPDIR}/graph8.json"
run_discover "${PROJECT}" --full > "${GRAPH_OUT}"

python3 -c "
import json, sys
with open('${GRAPH_OUT}') as f:
    g = json.load(f)
# Should have AlphaForced in exports
for nid, node in g['nodes'].items():
    exports = node.get('props', {}).get('exports', [])
    if 'AlphaForced' in exports:
        break
else:
    print('FAIL: AlphaForced not found in --full output')
    sys.exit(1)
" && pass "--full flag forces complete re-extraction" || fail "--full flag not working"


# ---------------------------------------------------------------------------
# Test 9: --incremental with no state falls back to full
# ---------------------------------------------------------------------------

echo "--- Test 9: --incremental with no state ---"
PROJECT="${TMPDIR}/test9"
setup_project "${PROJECT}"

# Run with --incremental but no state file
GRAPH_OUT="${TMPDIR}/graph9.json"
run_discover "${PROJECT}" --incremental > "${GRAPH_OUT}" 2>/dev/null

python3 -c "
import json, sys
with open('${GRAPH_OUT}') as f:
    g = json.load(f)
if len(g.get('nodes', {})) < 2:
    print(f'FAIL: expected >=2 nodes, got {len(g.get(\"nodes\", {}))}')
    sys.exit(1)
" && pass "--incremental with no state falls back to full" || fail "--incremental fallback failed"


# ---------------------------------------------------------------------------
# Test 10: State version mismatch triggers full fallback
# ---------------------------------------------------------------------------

echo "--- Test 10: State version mismatch ---"
PROJECT="${TMPDIR}/test10"
setup_project "${PROJECT}"

# Create state with wrong version
mkdir -p "${PROJECT}/.weld"
cat > "${PROJECT}/.weld/discovery-state.json" <<'JSON'
{
  "version": 999,
  "created_at": "2026-01-01T00:00:00Z",
  "files": {}
}
JSON

# Run discover (should fall back to full)
GRAPH_OUT="${TMPDIR}/graph10.json"
run_discover "${PROJECT}" > "${GRAPH_OUT}" 2>/dev/null

python3 -c "
import json, sys
with open('${GRAPH_OUT}') as f:
    g = json.load(f)
if len(g.get('nodes', {})) < 2:
    print(f'FAIL: expected >=2 nodes after version mismatch fallback')
    sys.exit(1)

# State should be overwritten with correct version
with open('${PROJECT}/.weld/discovery-state.json') as f:
    state = json.load(f)
if state['version'] != 1:
    print(f'FAIL: state not updated to version 1 after mismatch')
    sys.exit(1)
" && pass "version mismatch triggers full fallback and state update" || fail "version mismatch fallback failed"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "=== Incremental Discovery Test Results ==="
echo "  Passed: ${PASS_COUNT}"
echo "  Failed: ${FAIL_COUNT}"
echo ""

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
  echo "FAIL: ${FAIL_COUNT} test(s) failed"
  exit 1
fi

echo "PASS: all ${PASS_COUNT} tests passed"

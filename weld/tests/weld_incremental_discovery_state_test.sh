#!/usr/bin/env bash
set -euo pipefail

# weld_incremental_discovery_state_test: state-file creation and no-change
# idempotency for incremental discovery (content-hash state tracking, ADR 0008).
#
# Test scenarios:
#   1. Full discovery produces state file alongside graph
#   2. Incremental discovery with no changes returns same graph

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${SCRIPT_DIR}/weld_test_lib.sh"
# shellcheck source=weld/tests/_incremental_discovery_lib.sh
source "${SCRIPT_DIR}/_incremental_discovery_lib.sh"
ROOT="$(weld_test_repo_root "${SCRIPT_DIR}")"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

PASS_COUNT=0
FAIL_COUNT=0

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
if 'created_at' in state:
    errors.append('created_at is back: the inventory records claims about the '
                  'tree, not when the run happened, so a no-change discover '
                  'must not rewrite this tracked file (bd lrfu)')
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
# Summary
# ---------------------------------------------------------------------------

report_results "Incremental Discovery State"

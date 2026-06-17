#!/usr/bin/env bash
set -euo pipefail

# weld_incremental_discovery_fallback_test: full-discovery fallback and flag
# overrides for incremental discovery (content-hash state tracking, ADR 0008).
#
# Test scenarios:
#   6. Fallback to full when state file is missing
#   7. Fallback to full when state file is corrupt
#   8. --full flag forces full discovery even with valid state
#   9. --incremental flag with no state falls back to full
#  10. State file version mismatch triggers full fallback

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

report_results "Incremental Discovery Fallback"

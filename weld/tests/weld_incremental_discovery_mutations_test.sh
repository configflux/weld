#!/usr/bin/env bash
set -euo pipefail

# weld_incremental_discovery_mutations_test: file-mutation handling for
# incremental discovery (content-hash state tracking, ADR 0008).
#
# Test scenarios:
#   3. File modification triggers re-extraction of changed file only
#   4. File addition adds new nodes without losing existing ones
#   5. File deletion removes nodes sourced from deleted file

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
# Summary
# ---------------------------------------------------------------------------

report_results "Incremental Discovery Mutations"

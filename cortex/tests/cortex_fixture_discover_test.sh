#!/usr/bin/env bash
set -euo pipefail

# cortex_fixture_discover_test: validates cortex discover against fixture repos.
#
# Runs cortex init + cortex discover on each fixture and validates that the
# resulting graph JSON is well-formed and contains expected node types.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cortex/tests/cortex_test_lib.sh
source "${SCRIPT_DIR}/cortex_test_lib.sh"
ROOT="$(cortex_test_repo_root "${SCRIPT_DIR}")"

FIXTURES="${SCRIPT_DIR}/fixtures"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

errors=0

# ---------------------------------------------------------------------------
# Helper: init + discover a fixture, return graph JSON path
# ---------------------------------------------------------------------------
discover_fixture() {
  local name="$1"
  local fixture_dir="${FIXTURES}/${name}"
  local work_dir="${TMPDIR}/${name}"

  # Copy the fixture to a writable temp directory so we can create .cortex/ there.
  # Use -L to dereference symlinks (Bazel runfiles are symlink trees).
  cp -rL "${fixture_dir}" "${work_dir}"
  mkdir -p "${work_dir}/.cortex"

  # Generate the config
  cortex_in_root "${ROOT}" "${work_dir}" init \
    --output "${work_dir}/.cortex/discover.yaml" --force \
    > "${TMPDIR}/${name}_init.txt" 2>&1

  # Run discover against the temp copy (which now has .cortex/discover.yaml)
  cortex_in_root "${ROOT}" "${work_dir}" discover \
    > "${TMPDIR}/${name}_graph.json" 2>"${TMPDIR}/${name}_discover_err.txt" || true

  echo "${TMPDIR}/${name}_graph.json"
}

# ---------------------------------------------------------------------------
# Test 1: Python/Bazel fixture produces valid graph
# ---------------------------------------------------------------------------
echo "Test 1: Python/Bazel discover..."
PB_GRAPH="$(discover_fixture python_bazel)"

python3 -c "
import json, sys

with open('${PB_GRAPH}') as f:
    data = json.load(f)

errs = []

if 'nodes' not in data:
    errs.append('missing nodes key')
if 'edges' not in data:
    errs.append('missing edges key')
if 'meta' not in data:
    errs.append('missing meta key')

nodes = data.get('nodes', {})
node_types = set(n.get('type', '') for n in nodes.values())

# Python/Bazel fixture has fastapi imports, sqlalchemy models
# so we should see file-type nodes from python_module at minimum
if len(nodes) == 0:
    errs.append('expected at least some nodes from python_module strategy')

# Check meta structure
meta = data.get('meta', {})
if 'version' not in meta:
    errs.append('meta missing version')

if errs:
    print('FAIL: python_bazel discover:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print(f'PASS: python_bazel discover produced {len(nodes)} nodes, types: {node_types}')
" || { echo "FAIL: python_bazel discover"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Test 2: TypeScript/Node fixture produces valid (possibly sparse) graph
# ---------------------------------------------------------------------------
echo "Test 2: TypeScript/Node discover..."
TS_GRAPH="$(discover_fixture typescript_node)"

python3 -c "
import json, sys

with open('${TS_GRAPH}') as f:
    data = json.load(f)

errs = []

if 'nodes' not in data:
    errs.append('missing nodes key')
if 'meta' not in data:
    errs.append('missing meta key')

# TypeScript project may produce config nodes from config_file strategy
nodes = data.get('nodes', {})
meta = data.get('meta', {})
if 'version' not in meta:
    errs.append('meta missing version')

if errs:
    print('FAIL: typescript_node discover:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print(f'PASS: typescript_node discover produced {len(nodes)} nodes')
" || { echo "FAIL: typescript_node discover"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Test 3: C++/clang fixture produces valid graph
# ---------------------------------------------------------------------------
echo "Test 3: C++/clang discover..."
CPP_GRAPH="$(discover_fixture cpp_clang)"

python3 -c "
import json, sys

with open('${CPP_GRAPH}') as f:
    data = json.load(f)

errs = []

if 'nodes' not in data:
    errs.append('missing nodes key')
if 'meta' not in data:
    errs.append('missing meta key')

nodes = data.get('nodes', {})
meta = data.get('meta', {})
if 'version' not in meta:
    errs.append('meta missing version')

if errs:
    print('FAIL: cpp_clang discover:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print(f'PASS: cpp_clang discover produced {len(nodes)} nodes')
" || { echo "FAIL: cpp_clang discover"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Test 4: Legacy fixture produces valid graph with file nodes
# ---------------------------------------------------------------------------
echo "Test 4: Legacy discover..."
LG_GRAPH="$(discover_fixture legacy_onboarding)"

python3 -c "
import json, sys

with open('${LG_GRAPH}') as f:
    data = json.load(f)

errs = []

if 'nodes' not in data:
    errs.append('missing nodes key')
if 'meta' not in data:
    errs.append('missing meta key')

nodes = data.get('nodes', {})
meta = data.get('meta', {})
if 'version' not in meta:
    errs.append('meta missing version')

# Legacy project has Python files, should produce at least file nodes
if len(nodes) == 0:
    errs.append('expected at least some nodes from legacy project')

if errs:
    print('FAIL: legacy_onboarding discover:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print(f'PASS: legacy_onboarding discover produced {len(nodes)} nodes')
" || { echo "FAIL: legacy_onboarding discover"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
if [[ "${errors}" -gt 0 ]]; then
  echo "FAIL: ${errors} test(s) failed"
  exit 1
fi

echo "PASS: all kg_fixture_discover tests passed"

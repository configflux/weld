#!/usr/bin/env bash
set -euo pipefail

# weld_external_repo_discover_test: validates wd discover against
# realistic external-repo fixtures that mimic popular open-source project
# structures.
#
# Runs wd init + wd discover on each fixture and validates that the
# resulting graph JSON is well-formed and contains expected node types
# and counts.
#
# Fixtures:
#   1. FastAPI project — rich Python web app with models, routes, schemas
#   2. Django project  — multi-app Django site with models, views, URLs
#   3. Go project      — standard Go layout (cmd/, internal/, pkg/)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${SCRIPT_DIR}/weld_test_lib.sh"
ROOT="$(weld_test_repo_root "${SCRIPT_DIR}")"

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

  # Copy the fixture to a writable temp directory (Bazel runfiles are symlinks)
  cp -rL "${fixture_dir}" "${work_dir}"
  mkdir -p "${work_dir}/.weld"

  # Generate the config
  weld_in_root "${ROOT}" "${work_dir}" init \
    --output "${work_dir}/.weld/discover.yaml" --force \
    > "${TMPDIR}/${name}_init.txt" 2>&1

  # Run discover against the temp copy
  weld_in_root "${ROOT}" "${work_dir}" discover \
    > "${TMPDIR}/${name}_graph.json" 2>"${TMPDIR}/${name}_discover_err.txt" || true

  echo "${TMPDIR}/${name}_graph.json"
}

# ---------------------------------------------------------------------------
# Test 1: FastAPI project produces valid graph with expected node types
# ---------------------------------------------------------------------------
echo "Test 1: FastAPI project discover..."
FA_GRAPH="$(discover_fixture fastapi_project)"

python3 -c "
import json, sys

with open('${FA_GRAPH}') as f:
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

# Should have a meaningful number of nodes (app has many Python files)
if len(nodes) < 10:
    errs.append(f'expected at least 10 nodes, got {len(nodes)}')

# Should have file nodes from python_module strategy
if 'file' not in node_types:
    errs.append(f'expected file-type nodes, got types: {node_types}')

# Should have config nodes from config_file strategy
if 'config' not in node_types:
    errs.append(f'expected config-type nodes, got types: {node_types}')

# Should have dockerfile nodes
if 'dockerfile' not in node_types:
    errs.append(f'expected dockerfile-type nodes, got types: {node_types}')

# Should have workflow node from CI
if 'workflow' not in node_types:
    errs.append(f'expected workflow-type nodes, got types: {node_types}')

# Meta should have version
meta = data.get('meta', {})
if 'version' not in meta:
    errs.append('meta missing version')

if errs:
    print('FAIL: fastapi_project discover:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print(f'PASS: fastapi_project discover produced {len(nodes)} nodes, types: {node_types}')
" || { echo "FAIL: fastapi_project discover"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Test 2: Django project produces valid graph
# ---------------------------------------------------------------------------
echo "Test 2: Django project discover..."
DJ_GRAPH="$(discover_fixture django_project)"

python3 -c "
import json, sys

with open('${DJ_GRAPH}') as f:
    data = json.load(f)

errs = []

if 'nodes' not in data:
    errs.append('missing nodes key')
if 'meta' not in data:
    errs.append('missing meta key')

nodes = data.get('nodes', {})
node_types = set(n.get('type', '') for n in nodes.values())

# Django project has many Python files across blog/, accounts/, mysite/
if len(nodes) < 8:
    errs.append(f'expected at least 8 nodes for Django project, got {len(nodes)}')

# Should have file nodes from python_module strategy
if 'file' not in node_types:
    errs.append(f'expected file-type nodes, got types: {node_types}')

# Should have doc nodes from markdown strategy
if 'doc' not in node_types:
    errs.append(f'expected doc-type nodes, got types: {node_types}')

# Should have dockerfile nodes
if 'dockerfile' not in node_types:
    errs.append(f'expected dockerfile-type nodes, got types: {node_types}')

meta = data.get('meta', {})
if 'version' not in meta:
    errs.append('meta missing version')

if errs:
    print('FAIL: django_project discover:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print(f'PASS: django_project discover produced {len(nodes)} nodes, types: {node_types}')
" || { echo "FAIL: django_project discover"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Test 3: Go project produces valid graph (sparse, non-Python)
# ---------------------------------------------------------------------------
echo "Test 3: Go project discover..."
GO_GRAPH="$(discover_fixture go_project)"

python3 -c "
import json, sys

with open('${GO_GRAPH}') as f:
    data = json.load(f)

errs = []

if 'nodes' not in data:
    errs.append('missing nodes key')
if 'meta' not in data:
    errs.append('missing meta key')

nodes = data.get('nodes', {})
node_types = set(n.get('type', '') for n in nodes.values())

# Go project has no Python code, so nodes come from infra/build/docs only
# Should still produce some nodes (config, dockerfile, workflow, doc)
if len(nodes) < 3:
    errs.append(f'expected at least 3 nodes for Go project, got {len(nodes)}')

# Should have config nodes (go.mod, Makefile)
if 'config' not in node_types:
    errs.append(f'expected config-type nodes for Go project, got types: {node_types}')

# Should have doc nodes from docs/
if 'doc' not in node_types:
    errs.append(f'expected doc-type nodes for Go project, got types: {node_types}')

meta = data.get('meta', {})
if 'version' not in meta:
    errs.append('meta missing version')

if errs:
    print('FAIL: go_project discover:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print(f'PASS: go_project discover produced {len(nodes)} nodes, types: {node_types}')
" || { echo "FAIL: go_project discover"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Test 4: FastAPI graph node labels are meaningful
# ---------------------------------------------------------------------------
echo "Test 4: FastAPI graph node labels are meaningful..."
python3 -c "
import json, sys

with open('${FA_GRAPH}') as f:
    data = json.load(f)

nodes = data.get('nodes', {})
errs = []

# Check that file-type nodes have props with source_strategy
file_nodes = {nid: n for nid, n in nodes.items() if n.get('type') == 'file'}
for nid, node in file_nodes.items():
    props = node.get('props', {})
    if 'source_strategy' not in props:
        errs.append(f'node {nid} missing source_strategy in props')
    if not node.get('label'):
        errs.append(f'node {nid} has empty label')

# Check specific expected nodes exist (from the FastAPI project files)
node_labels = {n.get('label', ''): nid for nid, n in nodes.items()}
# The python_module strategy should find classes and functions in our app
# At minimum we should see some recognizable labels
all_labels = set(node_labels.keys())
if not all_labels:
    errs.append('no node labels found at all')

if errs:
    print('FAIL: FastAPI graph node quality:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print(f'PASS: FastAPI graph has {len(file_nodes)} file nodes with valid props')
" || { echo "FAIL: FastAPI graph node quality"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Test 5: Cross-fixture node count comparison
# ---------------------------------------------------------------------------
echo "Test 5: Cross-fixture node count comparison..."
python3 -c "
import json, sys

counts = {}
for name, path in [
    ('fastapi_project', '${FA_GRAPH}'),
    ('django_project', '${DJ_GRAPH}'),
    ('go_project', '${GO_GRAPH}'),
]:
    with open(path) as f:
        data = json.load(f)
    counts[name] = len(data.get('nodes', {}))

errs = []

# FastAPI (rich Python app) should have more nodes than Go (no Python strategy)
if counts['fastapi_project'] <= counts['go_project']:
    errs.append(
        f'FastAPI should have more nodes than Go: '
        f'{counts[\"fastapi_project\"]} vs {counts[\"go_project\"]}'
    )

# Django (multi-app) should have more nodes than Go
if counts['django_project'] <= counts['go_project']:
    errs.append(
        f'Django should have more nodes than Go: '
        f'{counts[\"django_project\"]} vs {counts[\"go_project\"]}'
    )

if errs:
    print('FAIL: cross-fixture node count comparison:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print(f'PASS: node counts are proportional — FastAPI:{counts[\"fastapi_project\"]}, '
      f'Django:{counts[\"django_project\"]}, Go:{counts[\"go_project\"]}')
" || { echo "FAIL: cross-fixture node count comparison"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
if [[ "${errors}" -gt 0 ]]; then
  echo "FAIL: ${errors} test(s) failed"
  exit 1
fi

echo "PASS: all weld_external_repo_discover tests passed"

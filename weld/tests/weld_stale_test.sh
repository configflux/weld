#!/usr/bin/env bash
set -euo pipefail

# wd stale test: exercises git SHA staleness tracking in discover + stale subcommand.

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${SCRIPT_DIR}/weld_test_lib.sh"
REPO_ROOT="$(weld_test_repo_root "${SCRIPT_DIR}")"

ROOT="${TMPDIR}/project"
mkdir -p "${ROOT}/.weld"

# --- Setup: create a git repo with a discover config ---
cd "${ROOT}"
git init --quiet
git config user.email "test@test.com"
git config user.name "Test"

# Create a minimal discover config and some source files.
# Track README.md via firstline_md so meta.discovered_from is non-empty --
# the ADR 0017 stale model gates on source file diffs (intersected with
# meta.discovered_from), so the fixture must actually track a file for
# test 3's "source change -> stale=true" assertion to be meaningful.
cat > "${ROOT}/.weld/discover.yaml" <<'YAML'
sources:
  - strategy: firstline_md
    glob: "README.md"
    kind: command
    type: readme
topology:
  nodes: []
  edges: []
YAML

echo "# hello" > "${ROOT}/README.md"
git add -A
git commit -m "initial" --quiet

HEAD_SHA="$(cd "${ROOT}" && git rev-parse HEAD)"

# --- Test 1: discover output includes meta.git_sha ---
echo "--- Test 1: discover output includes meta.git_sha ---"
weld_in_root "${REPO_ROOT}" "${ROOT}" discover > "${ROOT}/.weld/graph.json"

python3 -c "
import json, sys
with open('${ROOT}/.weld/graph.json') as f:
    data = json.load(f)
sha = data.get('meta', {}).get('git_sha')
assert sha is not None, f'meta.git_sha missing from discover output; meta={data.get(\"meta\")}'
assert sha == '${HEAD_SHA}', f'expected SHA ${HEAD_SHA}, got {sha}'
print('PASS: discover output includes meta.git_sha')
"

# --- Test 2: wd stale shows stale=false after fresh discover ---
echo "--- Test 2: wd stale shows stale=false after fresh discover ---"
out="$(weld_in_root "${REPO_ROOT}" "${ROOT}" stale)"
echo "${out}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['stale'] == False, f'expected stale=false, got {d}'
assert d['commits_behind'] == 0, f'expected commits_behind=0, got {d}'
assert d.get('graph_sha') == '${HEAD_SHA}', f'graph_sha mismatch: {d}'
print('PASS: wd stale shows stale=false after fresh discover')
"

# --- Test 3: wd stale shows stale=true after a new commit ---
echo "--- Test 3: wd stale shows stale=true after a new commit ---"
cd "${ROOT}"
echo "change" >> README.md
git add -A
git commit -m "dummy commit" --quiet
NEW_SHA="$(git rev-parse HEAD)"

out="$(weld_in_root "${REPO_ROOT}" "${ROOT}" stale)"
echo "${out}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['stale'] == True, f'expected stale=true, got {d}'
assert d['commits_behind'] == 1, f'expected commits_behind=1, got {d}'
assert d.get('graph_sha') == '${HEAD_SHA}', f'graph_sha should be old SHA: {d}'
assert d.get('current_sha') == '${NEW_SHA}', f'current_sha should be new SHA: {d}'
print('PASS: wd stale shows stale=true and commits_behind=1')
"

# --- Test 4: wd stale shows stale=false after re-discover ---
echo "--- Test 4: wd stale shows stale=false after re-discover ---"
cd "${ROOT}"
weld_in_root "${REPO_ROOT}" "${ROOT}" discover > "${ROOT}/.weld/graph.json"

out="$(weld_in_root "${REPO_ROOT}" "${ROOT}" stale)"
echo "${out}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['stale'] == False, f'expected stale=false after re-discover, got {d}'
assert d['commits_behind'] == 0, f'expected commits_behind=0, got {d}'
print('PASS: wd stale shows stale=false after re-discover')
"

# --- Test 5: wd stale in a non-git directory ---
echo "--- Test 5: wd stale in a non-git directory ---"
NON_GIT="${TMPDIR}/nogit"
mkdir -p "${NON_GIT}/.weld"

# Create a graph.json without git_sha (simulates discover in non-git env)
cat > "${NON_GIT}/.weld/graph.json" <<'JSON'
{
  "meta": {"version": 1, "updated_at": "2026-01-01T00:00:00+00:00"},
  "nodes": {},
  "edges": []
}
JSON

out="$(weld_in_root "${REPO_ROOT}" "${NON_GIT}" stale)"
echo "${out}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['stale'] == False, f'expected stale=false for non-git repo, got {d}'
assert 'reason' in d, f'expected reason key for non-git repo, got {d}'
assert 'not a git repo' in d['reason'].lower(), f'unexpected reason: {d[\"reason\"]}'
print('PASS: wd stale handles non-git directory correctly')
"

# --- Test 6: file-index.json includes meta.git_sha ---
echo "--- Test 6: file-index.json includes meta.git_sha ---"
cd "${ROOT}"
weld_in_root "${REPO_ROOT}" "${ROOT}" build-index 2>/dev/null

python3 -c "
import json
with open('${ROOT}/.weld/file-index.json') as f:
    data = json.load(f)
# New format has meta key
assert 'meta' in data, f'file-index.json should have meta key; keys={list(data.keys())[:5]}'
sha = data['meta'].get('git_sha')
assert sha is not None, f'meta.git_sha missing from file-index.json'
# files should also be present
assert 'files' in data, f'file-index.json should have files key'
print('PASS: file-index.json includes meta.git_sha')
"

# --- Test 7: find_files works with new file-index format ---
echo "--- Test 7: find_files works with new file-index format ---"
out="$(weld_in_root "${REPO_ROOT}" "${ROOT}" find README)"
echo "${out}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['query'] == 'README'
paths = [f['path'] for f in d['files']]
assert any('README' in p for p in paths), f'README not found in results: {paths}'
print('PASS: find_files works with new file-index format')
"

echo ""
echo "PASS: all weld_stale tests passed"

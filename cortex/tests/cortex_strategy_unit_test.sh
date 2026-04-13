#!/usr/bin/env bash
set -euo pipefail

# Unit tests for the cortex plugin strategy architecture.
# Tests: strategy loading, StrategyResult shape, shadow notice, _yaml module.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cortex/tests/cortex_test_lib.sh
source "${SCRIPT_DIR}/cortex_test_lib.sh"
ROOT="$(cortex_test_repo_root "${SCRIPT_DIR}")"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

errors=0

# --- Test 1: _yaml module is importable and parses basic YAML ---
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from cortex._yaml import parse_yaml
data = parse_yaml('key: value\nlist:\n  - a\n  - b')
assert data['key'] == 'value', f'expected value, got {data[\"key\"]}'
assert data['list'] == ['a', 'b'], f'expected [a,b], got {data[\"list\"]}'
print('PASS: _yaml parse_yaml works')
" || { echo "FAIL: _yaml module test"; errors=$((errors + 1)); }

# --- Test 1b: _yaml multi-line flow-style lists ---
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from cortex._yaml import parse_yaml
# mapping: 2-line and 3-line wrapping
d = parse_yaml('files: [\"a\", \"b\",\n  \"c\", \"d\"]\nother: val')
assert d['files'] == ['a', 'b', 'c', 'd'], f'2-line wrap: {d[\"files\"]}'
assert d['other'] == 'val'
d2 = parse_yaml('f: [\"x\",\n \"y\",\n \"z\"]\nn: ok')
assert d2['f'] == ['x', 'y', 'z'] and d2['n'] == 'ok', f'3-line: {d2}'
# sequence item: continuation on sub-key and on dash-key
d3 = parse_yaml('s:\n  - k: [\"a\",\n        \"b\"]\n    p: v')
assert d3['s'][0]['k'] == ['a', 'b'] and d3['s'][0]['p'] == 'v', f'seq: {d3}'
# single-line and empty still work
assert parse_yaml('i: [a, b]')['i'] == ['a', 'b']
assert parse_yaml('i: []')['i'] == []
print('PASS: _yaml multi-line flow-style lists')
" || { echo "FAIL: _yaml multi-line flow list test"; errors=$((errors + 1)); }

# --- Test 2: _helpers module is importable ---
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from cortex.strategies._helpers import base_names, inherits, tablename, extract_fks
print('PASS: _helpers module importable')
" || { echo "FAIL: _helpers import test"; errors=$((errors + 1)); }

# --- Test 3: Individual strategy files have extract() function ---
for strat in sqlalchemy fastapi pydantic worker_stage dockerfile compose \
             frontmatter_md firstline_md tool_script yaml_meta markdown \
             config_file python_module typescript_exports bazel manifest; do
  python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
import importlib
mod = importlib.import_module('cortex.strategies.${strat}')
assert hasattr(mod, 'extract'), '${strat} missing extract()'
import inspect
sig = inspect.signature(mod.extract)
params = list(sig.parameters.keys())
assert params == ['root', 'source', 'context'], \
    f'${strat} extract() params: {params}, expected [root, source, context]'
print('PASS: ${strat} has correct extract() signature')
" || { echo "FAIL: ${strat} strategy test"; errors=$((errors + 1)); }
done

# --- Test 4: StrategyResult shape ---
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from cortex.strategies._helpers import StrategyResult
r = StrategyResult(nodes={'a': {}}, edges=[{'x': 1}], discovered_from=['p'])
assert r.nodes == {'a': {}}
assert r.edges == [{'x': 1}]
assert r.discovered_from == ['p']
print('PASS: StrategyResult shape correct')
" || { echo "FAIL: StrategyResult test"; errors=$((errors + 1)); }

# --- Test 5: Orchestrator loads strategies by name ---
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
# Just verify the loader function works
from cortex.discover import _load_strategy
from pathlib import Path
fn = _load_strategy('sqlalchemy', Path('${ROOT}'))
assert callable(fn), 'should return callable'
print('PASS: _load_strategy returns callable')
" || { echo "FAIL: _load_strategy test"; errors=$((errors + 1)); }

# --- Test 6: Shadow notice to stderr ---
mkdir -p "${TMPDIR}/shadow_test/.cortex/strategies"
cat > "${TMPDIR}/shadow_test/.cortex/strategies/markdown.py" << 'PYEOF'
from cortex.strategies._helpers import StrategyResult

def extract(root, source, context):
    return StrategyResult(nodes={}, edges=[], discovered_from=[])
PYEOF

python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from cortex.discover import _load_strategy
from pathlib import Path
import io, contextlib
f = io.StringIO()
with contextlib.redirect_stderr(f):
    fn = _load_strategy('markdown', Path('${TMPDIR}/shadow_test'))
stderr_out = f.getvalue()
assert 'shadow' in stderr_out.lower() or 'override' in stderr_out.lower(), \
    f'expected shadow notice, got: {stderr_out!r}'
print('PASS: shadow notice emitted')
" || { echo "FAIL: shadow notice test"; errors=$((errors + 1)); }

# --- Test 7: typescript_exports strategy extracts exports from TS file ---
mkdir -p "${TMPDIR}/ts_test/src"
cat > "${TMPDIR}/ts_test/src/auth.ts" << 'TSEOF'
import { redirect } from "next/navigation";

export async function requestLoginLinkAction(formData: FormData): Promise<void> {
  const email = formData.get("email");
}

export const AUTH_HEADER = "x-auth-token";

export default function MainAuth() {
  return null;
}

function privateHelper() {
  return true;
}

export type UserSession = {
  actorId: string;
};
TSEOF

cat > "${TMPDIR}/ts_test/src/route.ts" << 'TSEOF'
import { NextRequest } from "next/server";

export async function GET(request: NextRequest) {
  return new Response("ok");
}

export async function POST(request: NextRequest) {
  return new Response("created");
}
TSEOF

cat > "${TMPDIR}/ts_test/src/Component.tsx" << 'TSEOF'
export default function Dashboard() {
  return <div>Hello</div>;
}

export function Sidebar() {
  return <nav>Nav</nav>;
}
TSEOF

python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from pathlib import Path
from cortex.strategies.typescript_exports import extract

root = Path('${TMPDIR}/ts_test')
source = {'glob': 'src/*.ts', 'id_prefix': 'web'}
result = extract(root, source, {})

# Should have two nodes (auth.ts and route.ts)
assert len(result.nodes) == 2, f'expected 2 nodes, got {len(result.nodes)}: {list(result.nodes.keys())}'

# Check auth.ts exports
auth_node = None
for nid, node in result.nodes.items():
    if 'auth' in nid:
        auth_node = node
        break
assert auth_node is not None, 'auth node not found'
exports = auth_node['props']['exports']
assert 'requestLoginLinkAction' in exports, f'missing requestLoginLinkAction in {exports}'
assert 'AUTH_HEADER' in exports, f'missing AUTH_HEADER in {exports}'
assert 'MainAuth' in exports, f'missing MainAuth in {exports}'
# privateHelper should NOT be in exports
assert 'privateHelper' not in exports, f'privateHelper should not be exported: {exports}'
assert auth_node['props']['line_count'] > 0, 'line_count should be > 0'
assert 'file' in auth_node['props'], 'file prop missing'

# Check route.ts has GET and POST
route_node = None
for nid, node in result.nodes.items():
    if 'route' in nid:
        route_node = node
        break
assert route_node is not None, 'route node not found'
route_exports = route_node['props']['exports']
assert 'GET' in route_exports, f'missing GET in {route_exports}'
assert 'POST' in route_exports, f'missing POST in {route_exports}'

print('PASS: typescript_exports extracts TS exports correctly')
" || { echo "FAIL: typescript_exports extract test"; errors=$((errors + 1)); }

# Test TSX extraction
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from pathlib import Path
from cortex.strategies.typescript_exports import extract

root = Path('${TMPDIR}/ts_test')
source = {'glob': 'src/*.tsx', 'id_prefix': 'web'}
result = extract(root, source, {})

assert len(result.nodes) == 1, f'expected 1 TSX node, got {len(result.nodes)}'
node = list(result.nodes.values())[0]
exports = node['props']['exports']
assert 'Dashboard' in exports, f'missing Dashboard in {exports}'
assert 'Sidebar' in exports, f'missing Sidebar in {exports}'

print('PASS: typescript_exports extracts TSX exports correctly')
" || { echo "FAIL: typescript_exports TSX test"; errors=$((errors + 1)); }

# Test recursive glob with **
mkdir -p "${TMPDIR}/ts_test/app/auth/consume"
cat > "${TMPDIR}/ts_test/app/auth/consume/route.ts" << 'TSEOF'
export async function GET(request: Request) {
  return new Response("consumed");
}
TSEOF

python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from pathlib import Path
from cortex.strategies.typescript_exports import extract

root = Path('${TMPDIR}/ts_test')
source = {'glob': 'app/**/*.ts', 'id_prefix': 'web'}
result = extract(root, source, {})

assert len(result.nodes) >= 1, f'expected >= 1 node from recursive glob, got {len(result.nodes)}'
node = list(result.nodes.values())[0]
assert 'GET' in node['props']['exports'], f'missing GET in recursive glob result'
assert len(result.discovered_from) > 0, 'discovered_from should be populated'

print('PASS: typescript_exports handles recursive glob')
" || { echo "FAIL: typescript_exports recursive glob test"; errors=$((errors + 1)); }

# Test empty file produces no node
cat > "${TMPDIR}/ts_test/src/empty.ts" << 'TSEOF'
// Just a comment, no exports
const internal = 42;
TSEOF

python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from pathlib import Path
from cortex.strategies.typescript_exports import extract

root = Path('${TMPDIR}/ts_test')
source = {'glob': 'src/empty.ts', 'id_prefix': 'web'}
result = extract(root, source, {})

# File with no exports should still produce no node (or an empty exports list)
# Since we want to match Python strategy behavior: skip files with no exports
assert len(result.nodes) == 0, f'expected 0 nodes for file with no exports, got {len(result.nodes)}'

print('PASS: typescript_exports skips files with no exports')
" || { echo "FAIL: typescript_exports empty exports test"; errors=$((errors + 1)); }

# --- Test 8: Orchestrator docstring enumerates context keys ---
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from cortex.discover import discover
doc = discover.__doc__ or ''
assert 'table_to_entity' in doc, 'docstring should mention table_to_entity'
assert 'pending_fk_edges' in doc, 'docstring should mention pending_fk_edges'
print('PASS: orchestrator docstring enumerates context keys')
" || { echo "FAIL: orchestrator docstring test"; errors=$((errors + 1)); }

# --- Test 9: typescript_exports listed in discover.yaml (monorepo only) ---
# This test validates that the monorepo's discover.yaml includes
# typescript_exports entries. In standalone cortex repos,
# there is no apps/web directory and thus no typescript_exports config,
# so this test is skipped.
if [[ -f "${ROOT}/.cortex/discover.yaml" && -d "${ROOT}/apps/web" ]]; then
  python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from cortex._yaml import parse_yaml
from pathlib import Path
config = parse_yaml(Path('${ROOT}/.cortex/discover.yaml').read_text(encoding='utf-8'))
sources = config.get('sources', [])
ts_sources = [s for s in sources if s.get('strategy') == 'typescript_exports']
assert len(ts_sources) >= 5, f'expected >= 5 typescript_exports sources in discover.yaml, got {len(ts_sources)}'
# Check expected globs are present
globs = {s['glob'] for s in ts_sources}
for expected in ['apps/web/app/**/*.ts', 'apps/web/app/**/*.tsx',
                 'apps/web/lib/*.ts', 'apps/web/components/*.tsx', 'e2e/**/*.ts']:
    assert expected in globs, f'missing glob {expected} in discover.yaml, found: {globs}'
print('PASS: discover.yaml has typescript_exports entries')
" || { echo "FAIL: discover.yaml typescript entries test"; errors=$((errors + 1)); }
else
  echo "SKIP: Test 9 (typescript_exports in discover.yaml) — not in monorepo context"
fi

# --- Test 10: Shared exclusion policy helpers ---
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from cortex.strategies._helpers import (
    is_excluded_dir_name, is_nested_repo_copy, filter_glob_results,
    EXCLUDED_DIR_NAMES, EXCLUDED_NESTED_REPO_SEGMENTS,
)
from pathlib import Path

# is_excluded_dir_name: known dirs
for name in ['.git', 'node_modules', '__pycache__', '.cortex', 'bazel-bin',
             'bazel-out', 'bazel-testlogs', 'bazel-project', '.worktrees']:
    assert is_excluded_dir_name(name), f'{name} should be excluded'

# is_excluded_dir_name: arbitrary bazel-* dirs
assert is_excluded_dir_name('bazel-foo'), 'bazel-foo should be excluded'
assert is_excluded_dir_name('bazel-genfiles'), 'bazel-genfiles should be excluded'

# is_excluded_dir_name: normal dirs should pass
for name in ['src', 'tools', 'services', 'docs', 'libs', '.claude']:
    assert not is_excluded_dir_name(name), f'{name} should NOT be excluded'

# is_nested_repo_copy: .claude/worktrees paths
assert is_nested_repo_copy(('.claude', 'worktrees', 'agent-abc', 'src', 'foo.py'))
assert is_nested_repo_copy(('.claude', 'worktrees'))
assert is_nested_repo_copy(('.claude', 'worktrees', 'branch1'))

# is_nested_repo_copy: should not match partial or unrelated
assert not is_nested_repo_copy(('.claude', 'agents', 'tdd.md'))
assert not is_nested_repo_copy(('src', 'services', 'api'))
assert not is_nested_repo_copy(('.claude',))
assert not is_nested_repo_copy(('worktrees',))

print('PASS: shared exclusion policy helpers work correctly')
" || { echo "FAIL: exclusion policy helpers test"; errors=$((errors + 1)); }

# --- Test 11: filter_glob_results removes worktree copies ---
mkdir -p "${TMPDIR}/filter_test/src"
echo "canonical" > "${TMPDIR}/filter_test/src/app.py"
mkdir -p "${TMPDIR}/filter_test/.claude/worktrees/agent-xyz/src"
echo "shadow" > "${TMPDIR}/filter_test/.claude/worktrees/agent-xyz/src/app.py"
mkdir -p "${TMPDIR}/filter_test/node_modules/pkg"
echo "dep" > "${TMPDIR}/filter_test/node_modules/pkg/index.py"

python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from cortex.strategies._helpers import filter_glob_results
from pathlib import Path

root = Path('${TMPDIR}/filter_test')
all_py = sorted(root.glob('**/*.py'))
filtered = filter_glob_results(root, all_py)
paths = [str(p.relative_to(root)) for p in filtered]

# Only the canonical file should remain
assert 'src/app.py' in paths, f'canonical src/app.py missing: {paths}'
assert not any('.claude/worktrees' in p for p in paths), f'worktree copy not filtered: {paths}'
assert not any('node_modules' in p for p in paths), f'node_modules not filtered: {paths}'
assert len(paths) == 1, f'expected 1 result, got {len(paths)}: {paths}'

print('PASS: filter_glob_results filters worktree copies and excluded dirs')
" || { echo "FAIL: filter_glob_results test"; errors=$((errors + 1)); }

# --- Test 12: Strategy _resolve_glob excludes worktree copies ---
mkdir -p "${TMPDIR}/strat_glob_test/services/worker/src/pkg"
echo "class Real: pass" > "${TMPDIR}/strat_glob_test/services/worker/src/pkg/real.py"
mkdir -p "${TMPDIR}/strat_glob_test/.claude/worktrees/agent-1/services/worker/src/pkg"
echo "class Shadow: pass" > "${TMPDIR}/strat_glob_test/.claude/worktrees/agent-1/services/worker/src/pkg/real.py"
# Need to initialize cortex importability
mkdir -p "${TMPDIR}/strat_glob_test/.cortex"

python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from pathlib import Path
from cortex.strategies.python_module import _resolve_glob

root = Path('${TMPDIR}/strat_glob_test')
matched, dirs = _resolve_glob(root, 'services/worker/src/**/*.py')
rel_paths = [str(p.relative_to(root)) for p in matched]

assert any('services/worker/src/pkg/real.py' in p for p in rel_paths), \
    f'canonical file missing: {rel_paths}'
assert not any('.claude/worktrees' in p for p in rel_paths), \
    f'worktree copy should be filtered: {rel_paths}'

print('PASS: strategy _resolve_glob excludes worktree copies')
" || { echo "FAIL: strategy _resolve_glob exclusion test"; errors=$((errors + 1)); }

if [[ "${errors}" -gt 0 ]]; then
  echo "FAIL: ${errors} test(s) failed"
  exit 1
fi

echo "PASS: all cortex strategy unit tests passed"
